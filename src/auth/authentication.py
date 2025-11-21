"""Lightweight Openbridge authentication middleware."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, List

from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import Middleware, MiddlewareContext

from .simple import OpenbridgeAuth, get_auth

logger = logging.getLogger(__name__)

JWT_CONTEXT_ATTR = "_openbridge_jwt"
JWT_PUBLIC_ATTR = "jwt_token"


def _set_context_state(ctx, key: str, value: str) -> None:
    """Best-effort state setter compatible with older FastMCP releases."""
    if not ctx:
        return

    setter = getattr(ctx, "set_state", None)
    if callable(setter):
        try:
            setter(key, value)
        except Exception:  # pragma: no cover - defensive
            logger.debug("Context set_state unavailable, falling back to attrs")

    setattr(ctx, key, value)


@dataclass
class AuthConfig:
    """Minimal configuration toggle for Openbridge authentication."""

    enabled: bool = True


def create_openbridge_config() -> AuthConfig:
    """Return an ``AuthConfig`` with Openbridge auth enabled."""
    return AuthConfig(enabled=True)


class OpenbridgeAuthMiddleware(Middleware):
    """Fetches a JWT from Openbridge and shares it with the FastMCP context."""

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
                if auth_header.startswith("Bearer "):
                    jwt_token = auth_header[7:].strip()
                    logger.debug("Using client-provided Bearer token (length: %d)", len(jwt_token))
        except Exception as exc:
            logger.debug("Could not extract client Authorization header: %s", exc)

        # Priority 2: Fall back to server's refresh token
        if not jwt_token:
            try:
                jwt_token = self._auth.get_jwt()
                logger.debug("Using server refresh token to generate JWT")
            except Exception:
                # Debug level: Some MCP endpoints (health, list tools) don't require auth
                logger.debug("No authentication configured (neither client token nor server refresh token)")

        # Share JWT with downstream tooling if available
        if jwt_token:
            _set_context_state(context.fastmcp_context, JWT_CONTEXT_ATTR, jwt_token)
            _set_context_state(context.fastmcp_context, JWT_PUBLIC_ATTR, jwt_token)

        return await call_next(context)


def create_auth_middleware(
    config: AuthConfig,
    *,
    jwt_middleware: bool = False,
    auth_manager: OpenbridgeAuth | None = None,
) -> List[Middleware]:
    """Return the middleware stack used by :mod:`src.server.mcp_server`.

    The historic implementation returned multiple middleware instances for
    refresh-token conversion and JWT validation.  We only need to ensure the
    Openbridge JWT is cached and injected into the FastMCP context now, so the
    stack consists of a single ``OpenbridgeAuthMiddleware`` instance.
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
