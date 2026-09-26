"""Path-based token middleware for Claude custom connector support.

Enables per-user MCP connection URLs of the form:
    https://mcp.example.com/mcp/{signed-jwt}

The signed JWT contains the Openbridge API credential as its ``sub`` claim,
signed with HMAC-SHA256 (HS256) using a shared secret. Any service that knows
the secret (``MCP_PATH_TOKEN_SECRET``) can generate connection URLs; the MCP
server verifies them on every inbound request.

Tokens require ``sub``, ``iat``, ``exp``, and ``aud: "openbridge-mcp"`` claims.
The server bounds both age and declared lifetime and requires ``sub`` to look
like an Openbridge API credential. ``iss`` remains intentionally unvalidated so
an approved external issuer with the shared secret can create tokens.

**Important:** This middleware must wrap the FastMCP ASGI app at the outermost
uvicorn level (``main.py``) so it intercepts before Starlette routing.
FastMCP's ``mcp.add_middleware()`` and ``server.run(middleware=[...])`` operate
inside the Starlette app, after which unregistered paths like ``/mcp/{token}``
would return 404 before the path can be rewritten.
"""
from __future__ import annotations

import datetime
import os
from typing import Optional

import jwt as pyjwt

from src.auth.simple import is_refresh_token

MCP_MOUNT = "mcp"
DEFAULT_TTL_DAYS = 7
DEFAULT_MAX_AGE_DAYS = 7
MAX_CLOCK_SKEW_SECONDS = 60
ISSUER = "openbridge-mcp"
AUDIENCE = "openbridge-mcp"


def load_secret() -> str:
    """Load ``MCP_PATH_TOKEN_SECRET`` from the environment.

    Startup policy validates strength and rejects missing or placeholder
    values before this helper is called.
    """
    secret = os.getenv("MCP_PATH_TOKEN_SECRET", "").strip()
    if secret:
        return secret
    raise RuntimeError("MCP_PATH_TOKEN_SECRET is required when path tokens are enabled")


class PathTokenMiddleware:
    """Pure ASGI middleware that extracts a signed HS256 JWT from the URL path
    and injects the embedded API credential as an ``Authorization: Bearer``
    header before Starlette's router runs.

    Starlette instantiates this class as
    ``PathTokenMiddleware(app, secret=secret)``
    when it is passed via ``Middleware(PathTokenMiddleware, secret=...)``.
    """

    def __init__(self, app, *, secret: str) -> None:
        self.app = app
        self._secret = secret

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "http":
            path: str = scope.get("path", "")
            parts = path.strip("/").split("/")
            has_auth = any(
                k.lower() == b"authorization"
                for k, _ in scope.get("headers", [])
            )
            if len(parts) >= 2 and parts[0] == MCP_MOUNT and not has_auth:
                refresh_token = _verify_path_token(parts[1], self._secret)
                if refresh_token:
                    new_path = "/" + "/".join([MCP_MOUNT] + parts[2:])
                    scope["path"] = new_path
                    scope["raw_path"] = new_path.encode()
                    # Append in place so existing objects holding a reference
                    # to scope["headers"] (e.g. a cached Starlette Headers
                    # wrapper) see the new entry without a list replacement.
                    scope["headers"].append(
                        (b"authorization", f"Bearer {refresh_token}".encode())
                    )
        await self.app(scope, receive, send)

    def generate_token(self, refresh_token: str, ttl_days: Optional[int] = None) -> str:
        """Sign a path token containing the given API credential."""
        return _sign_path_token(refresh_token, self._secret, ttl_days)


def _sign_path_token(
    refresh_token: str,
    secret: str,
    ttl_days: Optional[int] = None,
) -> str:
    days = (
        ttl_days
        if ttl_days is not None
        else int(os.getenv("MCP_PATH_TOKEN_TTL_DAYS", DEFAULT_TTL_DAYS))
    )
    now = datetime.datetime.now(datetime.UTC)
    payload = {
        "sub": refresh_token,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + datetime.timedelta(days=days),
    }
    return pyjwt.encode(payload, secret, algorithm="HS256")


def _verify_path_token(token: str, secret: str) -> Optional[str]:
    """Verify a bounded HS256 path token and return its API credential.

    The ``iss`` claim is not validated; an approved service sharing the secret
    may issue tokens. Required claims, signature, audience, expiry, subject
    shape, age, and declared lifetime are enforced.
    """
    try:
        payload = pyjwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=AUDIENCE,
            leeway=MAX_CLOCK_SKEW_SECONDS,
            options={"require": ["sub", "iat", "exp", "aud"]},
        )
        issued_at = float(payload["iat"])
        expires_at = float(payload["exp"])
        configured_days = int(
            os.getenv("MCP_PATH_TOKEN_MAX_AGE_DAYS", DEFAULT_MAX_AGE_DAYS)
        )
        if configured_days <= 0:
            return None
        max_age_seconds = min(configured_days, DEFAULT_MAX_AGE_DAYS) * 86400
        if datetime.datetime.now(datetime.UTC).timestamp() - issued_at > max_age_seconds:
            return None
        if expires_at - issued_at > max_age_seconds:
            return None
        subject = payload["sub"]
        if not isinstance(subject, str) or not is_refresh_token(subject):
            return None
        return subject
    except (pyjwt.PyJWTError, TypeError, ValueError):
        return None


def build_connection_url(base_url: str, refresh_token: str, secret: str) -> str:
    """Generate a Claude-compatible MCP connection URL with an embedded signed JWT.

    The JWT is signed with HS256 using ``secret``, which must match
    ``MCP_PATH_TOKEN_SECRET`` on the MCP server.

    Args:
        base_url: Public base URL of the MCP server (e.g. ``https://mcp.example.com``).
        refresh_token: Openbridge API credential in ``xxx:yyy`` format.
        secret: Shared signing secret (value of ``MCP_PATH_TOKEN_SECRET``).

    Returns:
        A URL that can be pasted directly into Claude's custom connector field.
    """
    token = _sign_path_token(refresh_token, secret)
    return f"{base_url.rstrip('/')}/mcp/{token}"


__all__ = [
    "AUDIENCE",
    "PathTokenMiddleware",
    "build_connection_url",
    "load_secret",
]
