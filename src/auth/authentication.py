"""Lightweight Openbridge authentication middleware."""

from __future__ import annotations

import logging
import os
from inspect import isawaitable
from dataclasses import dataclass
from typing import Iterable, List

from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import Middleware, MiddlewareContext

from .session_state import set_request_jwt
from .simple import OpenbridgeAuth, get_auth, is_refresh_token

logger = logging.getLogger(__name__)

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


@dataclass
class AuthConfig:
    """Configuration for Openbridge authentication."""

    enabled: bool = True
    refresh_token_enabled: bool = True
    jwt_validation_enabled: bool = True
    jwt_verify_signature: bool = True


def create_openbridge_config() -> AuthConfig:
    """Return an AuthConfig, reading AUTH_ENABLED from the environment.

    When AUTH_ENABLED is set to 'false' (case-insensitive), authentication
    middleware is disabled. Used for local development and testing.
    """
    enabled = os.getenv("AUTH_ENABLED", "true").lower() != "false"
    return AuthConfig(
        enabled=enabled,
        refresh_token_enabled=enabled,
        jwt_validation_enabled=enabled,
        jwt_verify_signature=True,
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

    def __init__(self, auth: OpenbridgeAuth):
        super().__init__()
        self._auth = auth

    async def on_request(self, context: MiddlewareContext, call_next):
        if not context.fastmcp_context:
            return await call_next(context)

        jwt_token = None

        # Priority 1: Check for client-provided Authorization header
        try:
            http_request = get_http_request()
            if http_request:
                auth_header = http_request.headers.get("authorization", "")
                if auth_header.lower().startswith("bearer "):
                    client_token = auth_header.split(" ", 1)[1].strip()
                    if client_token:
                        jwt_token = self._resolve_client_token(client_token)
        except Exception as exc:
            logger.warning("Could not extract client Authorization header: %s", exc)

        # Priority 2: Fall back to server's refresh token
        if not jwt_token:
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
        """
        if is_refresh_token(token):
            logger.debug("Client token is a refresh token — exchanging for JWT")
            return self._auth.exchange_token(token)

        logger.debug("Client token appears to be a JWT — using directly (length: %d)", len(token))
        return token


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
    return [OpenbridgeAuthMiddleware(auth)]


__all__: Iterable[str] = [
    "AuthConfig",
    "OpenbridgeAuthMiddleware",
    "create_auth_middleware",
    "create_openbridge_config",
    "JWT_CONTEXT_ATTR",
    "JWT_PUBLIC_ATTR",
]
