"""Starlette middleware stack for verifying Openbridge tokens via FastMCP."""

from __future__ import annotations

import logging
from typing import List, Optional

import jwt
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend
from mcp.server.auth.provider import AccessToken
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware

from src.auth.simple import AuthenticationError, OpenbridgeAuth, get_auth

logger = logging.getLogger(__name__)


def _is_refresh_token(token: str) -> bool:
    """Heuristic for refresh tokens (format xxx:yyy)."""
    return ":" in token and len(token.split(":", maxsplit=1)) == 2


def _extract_scopes(payload: dict) -> list[str]:
    """Normalize scope values from JWT payload."""
    scope_value = payload.get("scope") or payload.get("scopes")
    if isinstance(scope_value, str):
        return [scope for scope in scope_value.split() if scope]
    if isinstance(scope_value, list):
        return [scope for scope in scope_value if isinstance(scope, str)]
    return []


class OpenbridgeTokenProvider:
    """Minimal provider compatible with FastMCP's BearerAuthBackend."""

    def __init__(self, auth: OpenbridgeAuth):
        self._auth = auth

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        """Validate or exchange the provided token and return an AccessToken."""
        try:
            jwt_token = (
                await self._auth.get_jwt_async(refresh_token=token)
                if _is_refresh_token(token)
                else token
            )
        except AuthenticationError as exc:
            logger.warning("Openbridge token exchange failed: %s", exc)
            return None
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Unexpected token exchange failure: %s", exc)
            return None

        try:
            payload = jwt.decode(jwt_token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            logger.warning("Invalid JWT supplied to Openbridge token verifier: %s", exc)
            return None

        expires_at = payload.get("exp")
        client_id = str(payload.get("sub") or payload.get("client_id") or "openbridge-client")
        scopes = _extract_scopes(payload)

        return AccessToken(
            token=jwt_token,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(expires_at) if isinstance(expires_at, (int, float)) else None,
        )


def create_token_middleware(
    auth: Optional[OpenbridgeAuth] = None,
) -> List[Middleware]:
    """Return Starlette middleware enforcing Bearer token authentication."""
    auth = auth or get_auth()
    provider = OpenbridgeTokenProvider(auth)

    return [
        Middleware(
            AuthenticationMiddleware,
            backend=BearerAuthBackend(token_verifier=provider),
        ),
        Middleware(AuthContextMiddleware),
    ]


__all__ = ["create_token_middleware", "OpenbridgeTokenProvider"]
