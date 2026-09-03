from __future__ import annotations

import os
from inspect import isawaitable
from typing import Dict, Optional
from urllib.parse import urljoin, urlparse

from fastmcp.server.dependencies import get_access_token

from src.auth.authentication import JWT_CONTEXT_ATTR, JWT_PUBLIC_ATTR
from src.auth.session_state import get_request_jwt
from src.auth.simple import AuthenticationError, get_api_timeout, get_auth
from src.utils.logging import get_logger
from src.utils.security import ValidationError, validate_url

logger = get_logger("base_tools")


def _require_client_auth_enabled() -> bool:
    """Return True when ``OPENBRIDGE_REQUIRE_CLIENT_AUTH`` is set truthy.

    Read at call time rather than import time so test fixtures can
    monkeypatch the env per-test without resetting module state.
    """
    raw = os.getenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH")
    if not raw:
        return False
    return raw.strip().lower() in {"true", "1", "yes", "on"}


def _get_context_jwt(ctx) -> Optional[str]:
    """Best-effort retrieval of a primed JWT from the FastMCP context.

    Priority:
    1. ContextVar set by the auth middleware (survives across fresh
       Context instances, e.g. those handed to tools invoked from the
       Code Mode sandbox).
    2. ``ctx.get_state`` (older FastMCP sync variant; async results
       are discarded to avoid coroutine leakage).
    3. Attribute fallback written by the middleware.
    """
    cv_token = get_request_jwt()
    if cv_token and isinstance(cv_token, str):
        return cv_token

    # FastMCP 4 restores this standard token inside background task workers.
    # Refresh-token middleware bridges into it at task submission time, while
    # OAuthProxy populates it directly after token verification.
    access_token = get_access_token()
    if access_token and isinstance(access_token.token, str):
        return access_token.token

    if not ctx:
        return None

    get_state = getattr(ctx, "get_state", None)
    if callable(get_state):
        try:
            candidate = get_state(JWT_PUBLIC_ATTR)
            if candidate and not isawaitable(candidate):
                return candidate
        except Exception:  # pragma: no cover - defensive
            logger.debug("Context get_state accessor is unavailable")

    jwt_token = getattr(ctx, JWT_CONTEXT_ATTR, None) or getattr(ctx, JWT_PUBLIC_ATTR, None)
    if jwt_token and isinstance(jwt_token, str):
        return jwt_token

    return None


def get_auth_headers(ctx=None) -> Dict[str, str]:
    """Return Authorization headers for Openbridge API calls."""
    jwt_token = _get_context_jwt(ctx)
    if jwt_token:
        logger.debug(
            "Using JWT token from context (len=%d, segments=%d, prefix=%s…)",
            len(jwt_token),
            jwt_token.count(".") + 1,
            jwt_token[:12],
        )
        return {"Authorization": f"Bearer {jwt_token}"}

    # Multi-tenant fail-closed backstop: if the deployment requires
    # per-tenant auth, never silently fall back to the server token or
    # an empty header. The middleware is the primary gate, but tools
    # invoked without a request-scoped context (e.g. internal callers,
    # background jobs) must also refuse to leak the server principal.
    if _require_client_auth_enabled():
        raise AuthenticationError(
            "OPENBRIDGE_REQUIRE_CLIENT_AUTH is enabled but no per-tenant "
            "JWT was resolved for this call. Refusing to fall back to the "
            "server refresh token. Ensure the request carries an "
            "Authorization: Bearer header."
        )

    try:
        auth = get_auth()
    except AuthenticationError as exc:
        logger.warning("Openbridge auth disabled: %s", exc)
        return {}

    try:
        return auth.get_headers()
    except AuthenticationError as exc:
        # If refresh token is not available, return empty headers (no auth)
        if "not available" in str(exc):
            logger.debug("OPENBRIDGE_REFRESH_TOKEN not available, proceeding without auth")
            return {}

        # For other auth errors (conversion failures, network issues), raise detailed error
        auth_url = f"{auth.auth_base_url}/auth/api/ref"
        logger.error("Authentication failed: %s", exc)
        raise AuthenticationError(
            "Failed to convert OPENBRIDGE_REFRESH_TOKEN to JWT.\n\n"
            "Possible causes:\n"
            "1. Token format incorrect (expected: xxx:yyy)\n"
            "2. OpenBridge API unreachable\n"
            "3. Token expired or revoked\n\n"
            f"Action: Verify OPENBRIDGE_REFRESH_TOKEN and check connectivity to {auth_url}"
        ) from exc


def safe_pagination_url(next_url: Optional[str], base_url: str) -> Optional[str]:
    """Ensure pagination links stay on the expected host."""
    if not next_url:
        return None

    candidate = urljoin(base_url, next_url)
    try:
        validate_url(candidate, allowed_schemes=["https"])
    except ValidationError as exc:
        logger.warning("SSRF blocked: invalid pagination URL (%s)", exc)
        return None

    expected_host = urlparse(base_url).netloc
    actual_host = urlparse(candidate).netloc

    if actual_host and expected_host and actual_host != expected_host:
        logger.warning(
            "SSRF blocked: unexpected pagination host %s (expected %s)",
            actual_host,
            expected_host,
        )
        return None

    return candidate


__all__ = ["get_auth_headers", "get_api_timeout", "safe_pagination_url", "AuthenticationError"]
