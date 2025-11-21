from __future__ import annotations

from typing import Dict, Optional
from urllib.parse import urljoin, urlparse

from mcp.server.auth.middleware.auth_context import get_access_token

from src.auth.simple import AuthenticationError, get_api_timeout, get_auth
from src.utils.logging import get_logger
from src.utils.security import ValidationError, validate_url

logger = get_logger("base_tools")


def _get_context_jwt(ctx) -> Optional[str]:
    """Best-effort retrieval of a primed JWT from the FastMCP context."""
    token = get_access_token()
    if token:
        return token.token

    return None


def get_auth_headers(ctx=None) -> Dict[str, str]:
    """Return Authorization headers for Openbridge API calls."""
    jwt_token = _get_context_jwt(ctx)
    if jwt_token:
        logger.debug("Using JWT token from context")
        return {"Authorization": f"Bearer {jwt_token}"}

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
