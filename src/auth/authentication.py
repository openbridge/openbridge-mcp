"""Lightweight Openbridge authentication middleware."""

from __future__ import annotations

import logging
import os
from inspect import isawaitable
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import jwt as pyjwt
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp import McpError
from mcp.types import ErrorData

from .session_state import set_request_jwt
from .simple import AuthenticationError, OpenbridgeAuth, get_auth, is_refresh_token

logger = logging.getLogger(__name__)

# JSON-RPC error code used for authentication failures. The MCP spec reserves
# -32000..-32099 for server-defined errors; -32001 is our convention for
# "auth failed — credential rejected by upstream".
AUTH_ERROR_CODE = -32001

JWT_CONTEXT_ATTR = "_openbridge_jwt"
JWT_PUBLIC_ATTR = "jwt_token"


async def _set_context_state(ctx, key: str, value: str) -> None:
    """Best-effort state setter compatible with older FastMCP releases."""
    if not ctx:
        return

    setter = getattr(ctx, "set_state", None)
    if callable(setter):
        try:
            maybe_awaitable = setter(key, value)
            if isawaitable(maybe_awaitable):
                await maybe_awaitable
        except Exception:  # pragma: no cover - defensive
            logger.debug("Context set_state unavailable, falling back to attrs")

    setattr(ctx, key, value)


_VALID_AUTH_MODES = {"refresh_token", "oauth_proxy"}


@dataclass
class AuthConfig:
    """Configuration for Openbridge authentication."""

    enabled: bool = True
    refresh_token_enabled: bool = True
    jwt_validation_enabled: bool = True
    jwt_verify_signature: bool = True
    # Multi-tenant safety: when True, requests without a client-provided
    # Authorization header are rejected outright rather than falling back
    # to the server's OPENBRIDGE_REFRESH_TOKEN. Set this in any deployment
    # that serves more than one tenant from a shared instance.
    require_client_auth: bool = False
    # Auth mode: "refresh_token" (default) uses OpenbridgeAuthMiddleware;
    # "oauth_proxy" uses FastMCP's built-in OAuthProxy with introspection.
    auth_mode: str = "refresh_token"


def _env_flag(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable.

    Accepts ``true``/``false``/``1``/``0``/``yes``/``no`` (case-insensitive).
    Anything else falls back to *default* — boot-time config typos must not
    silently flip security-relevant flags.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    logger.warning(
        "%s=%r is not a recognized boolean; falling back to default %s",
        name, raw, default,
    )
    return default


def _parse_auth_mode(raw: str | None) -> str:
    """Return a validated auth mode string, falling back to 'refresh_token' on bad input."""
    if raw is None:
        return "refresh_token"
    normalized = raw.strip().lower()
    if normalized in _VALID_AUTH_MODES:
        return normalized
    logger.warning(
        "OPENBRIDGE_AUTH_MODE=%r is not a recognized mode %s; "
        "falling back to 'refresh_token'",
        raw,
        sorted(_VALID_AUTH_MODES),
    )
    return "refresh_token"


def create_openbridge_config() -> AuthConfig:
    """Return an AuthConfig, reading AUTH_ENABLED from the environment.

    When AUTH_ENABLED is set to 'false' (case-insensitive), authentication
    middleware is disabled. Used for local development and testing.

    When ``OPENBRIDGE_REQUIRE_CLIENT_AUTH`` is true, the middleware fails
    closed for any request without an ``Authorization: Bearer`` header
    rather than executing as the server's principal. Required for any
    multi-tenant deployment.

    When ``OPENBRIDGE_AUTH_MODE`` is ``"oauth_proxy"``, FastMCP's built-in
    OAuthProxy is used instead of ``OpenbridgeAuthMiddleware``.
    """
    enabled = os.getenv("AUTH_ENABLED", "true").lower() != "false"
    auth_mode = _parse_auth_mode(os.getenv("OPENBRIDGE_AUTH_MODE"))
    return AuthConfig(
        enabled=enabled,
        refresh_token_enabled=enabled,
        jwt_validation_enabled=enabled,
        jwt_verify_signature=True,
        require_client_auth=_env_flag("OPENBRIDGE_REQUIRE_CLIENT_AUTH", default=False),
        auth_mode=auth_mode,
    )


class OpenbridgeAuthMiddleware(Middleware):
    """Extracts or exchanges tokens and shares the resulting JWT with the FastMCP context.

    Supports two client-side token formats:

    * **Refresh token** (``xxx:yyy``): The middleware exchanges it for a
      short-lived JWT via the Openbridge auth API, caching the result so
      repeated tool calls within the same session are fast.
    * **JWT** (three dot-separated segments): Used as-is — no exchange
      required.

    When no client token is present the middleware falls back to the
    server-side ``OPENBRIDGE_REFRESH_TOKEN`` environment variable.
    """

    def __init__(self, auth: OpenbridgeAuth, *, require_client_auth: bool = False):
        super().__init__()
        self._auth = auth
        self._require_client_auth = require_client_auth

    async def on_request(self, context: MiddlewareContext, call_next):
        if not context.fastmcp_context:
            return await call_next(context)

        jwt_token = None
        client_token: Optional[str] = None
        http_request_seen = False

        # Priority 1: Extract a client-provided Authorization header.
        # Header extraction itself must not raise — unauthenticated endpoints
        # (health, list tools) should still work without a Bearer token.
        try:
            http_request = get_http_request()
            if http_request:
                http_request_seen = True
                auth_header = http_request.headers.get("authorization", "")
                if auth_header.lower().startswith("bearer "):
                    candidate = auth_header.split(" ", 1)[1].strip()
                    if candidate:
                        client_token = candidate
        except Exception as exc:
            # Header extraction failure is benign — fall back to server token.
            logger.warning("Could not extract client Authorization header: %s", exc)

        # Priority 1b: Resolve the client token into a JWT. A failure here
        # (refresh-token rejected, auth API unreachable, malformed response)
        # is NOT benign — surface it as a JSON-RPC auth error so the client
        # knows its credential was rejected instead of silently falling back
        # to the server token and returning data for the wrong account.
        if client_token:
            try:
                jwt_token = self._resolve_client_token(client_token)
            except AuthenticationError as exc:
                logger.warning("Client token exchange failed: %s", exc)
                raise McpError(
                    ErrorData(
                        code=AUTH_ERROR_CODE,
                        message=(
                            "Client-provided Openbridge token could not be exchanged for a JWT. "
                            "Verify the Authorization: Bearer header carries a valid refresh token "
                            "(format: xxx:yyy) or an unexpired JWT."
                        ),
                    )
                ) from exc

        # Multi-tenant fail-closed: when require_client_auth is on and we
        # actually saw an HTTP request (so this isn't a stdio/list_tools
        # call) but no client token was provided, refuse to fall back to
        # the server's refresh token. Executing under a shared principal
        # in a multi-tenant deployment is a cross-account data leak.
        if (
            not client_token
            and self._require_client_auth
            and http_request_seen
            and _parse_auth_mode(os.getenv("OPENBRIDGE_AUTH_MODE")) != "oauth_proxy"
        ):
            logger.warning(
                "Rejecting request: OPENBRIDGE_REQUIRE_CLIENT_AUTH=true and no Bearer token present"
            )
            raise McpError(
                ErrorData(
                    code=AUTH_ERROR_CODE,
                    message=(
                        "This server requires a per-tenant Authorization: Bearer "
                        "credential. Server-token fallback is disabled because "
                        "OPENBRIDGE_REQUIRE_CLIENT_AUTH is enabled."
                    ),
                )
            )

        # Priority 2: Fall back to server's refresh token only when there
        # was no client credential at all and the deployment hasn't opted
        # into strict per-tenant auth.
        if not jwt_token and not client_token:
            try:
                jwt_token = self._auth.get_jwt()
                logger.debug("Using server refresh token to generate JWT")
            except Exception:
                # Debug level: Some MCP endpoints (health, list tools) don't require auth
                logger.debug("No authentication configured (neither client token nor server refresh token)")

        # Share JWT with downstream tooling if available.
        # Uses both FastMCP context state *and* a ContextVar so tools
        # invoked from Code Mode's sandbox (which receive a fresh
        # Context instance) can still see the resolved token.
        if jwt_token:
            _log_jwt_identity(jwt_token)
            set_request_jwt(jwt_token)
            await _set_context_state(context.fastmcp_context, JWT_CONTEXT_ATTR, jwt_token)
            await _set_context_state(context.fastmcp_context, JWT_PUBLIC_ATTR, jwt_token)
        else:
            set_request_jwt(None)

        return await call_next(context)

    def _resolve_client_token(self, token: str) -> str:
        """Turn a client-provided token into a usable JWT.

        If the token looks like an Openbridge refresh token (``xxx:yyy``)
        it is exchanged for a JWT via the auth API.  Otherwise it is
        assumed to already be a JWT and is returned as-is.

        Raises:
            AuthenticationError: If the refresh token cannot be exchanged.
        """
        if is_refresh_token(token):
            logger.debug("Client token is a refresh token — exchanging for JWT")
            return self._auth.exchange_token(token)

        logger.debug("Client token appears to be a JWT — using directly (length: %d)", len(token))
        return token


def _safe_decode_claims(jwt_token: str) -> Dict[str, Any]:
    """Decode a JWT's claims *without* verifying the signature.

    For logging / diagnostic use only. Signature verification and
    authorization are enforced by downstream Openbridge APIs, not here —
    we just want to know which account the JWT is asserting so operators
    can tell client tokens apart from server fallbacks.
    """
    try:
        return pyjwt.decode(jwt_token, options={"verify_signature": False}) or {}
    except Exception:  # pragma: no cover - defensive
        return {}


def _log_jwt_identity(jwt_token: str) -> None:
    """Emit a debug-level line identifying the JWT's subject/account.

    No-op when the debug log level is disabled so we don't pay the decode
    cost in production.
    """
    if not logger.isEnabledFor(logging.DEBUG):
        return
    claims = _safe_decode_claims(jwt_token)
    account_id = (
        claims.get("account_id")
        or claims.get("account")
        or claims.get("aid")
    )
    subject = claims.get("sub") or claims.get("user_id") or claims.get("uid")
    logger.debug(
        "JWT identity: sub=%s account_id=%s exp=%s",
        subject,
        account_id,
        claims.get("exp") or claims.get("expires_at"),
    )


def create_auth_middleware(
    config: AuthConfig,
    *,
    jwt_middleware: bool = False,
    auth_manager: OpenbridgeAuth | None = None,
) -> List[Middleware]:
    """Return the middleware stack used by :mod:`src.server.mcp_server`.

    The middleware detects whether a client-provided Bearer token is a
    refresh token or an already-exchanged JWT.  Refresh tokens are
    converted to JWTs via the Openbridge auth API; JWTs are passed
    through unchanged.  When no client token is present the server's
    ``OPENBRIDGE_REFRESH_TOKEN`` is used as a fallback.
    """
    if not config.enabled:
        return []

    auth = auth_manager or get_auth()
    return [
        OpenbridgeAuthMiddleware(
            auth,
            require_client_auth=config.require_client_auth,
        )
    ]


__all__: Iterable[str] = [
    "AuthConfig",
    "OpenbridgeAuthMiddleware",
    "create_auth_middleware",
    "create_openbridge_config",
    "JWT_CONTEXT_ATTR",
    "JWT_PUBLIC_ATTR",
]
