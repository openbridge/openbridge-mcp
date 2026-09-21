"""Shared startup policy for security-sensitive runtime settings."""

from ipaddress import ip_address
import os

from src.utils.logging import get_logger

logger = get_logger("runtime_security")

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def env_flag(name: str, *, default: bool) -> bool:
    """Parse an environment boolean, using the safe default for bad input."""
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    logger.warning(
        "%s=%r is not a recognized boolean; falling back to default %s",
        name,
        raw,
        default,
    )
    return default


def require_client_auth_enabled() -> bool:
    """Return whether every remote request must supply its own credential."""
    return env_flag("OPENBRIDGE_REQUIRE_CLIENT_AUTH", default=True)


def is_loopback_host(host: str) -> bool:
    """Return True for localhost and every IPv4 or IPv6 loopback address."""
    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized.strip("[]")).is_loopback
    except ValueError:
        return False


def validate_runtime_security(host: str) -> None:
    """Reject remotely reachable authentication bypasses unless overridden."""
    auth_enabled = env_flag("AUTH_ENABLED", default=True)
    client_auth = require_client_auth_enabled()
    server_token = bool(os.getenv("OPENBRIDGE_REFRESH_TOKEN", "").strip())
    unsafe_override = env_flag(
        "OPENBRIDGE_ALLOW_INSECURE_REMOTE_AUTH",
        default=False,
    )
    unsafe_remote = not is_loopback_host(host) and (
        not auth_enabled or (server_token and not client_auth)
    )
    if not unsafe_remote:
        return
    if not unsafe_override:
        raise RuntimeError(
            "Unsafe remote client authentication configuration: bind to a "
            "loopback address, enable authentication, or explicitly set "
            "OPENBRIDGE_ALLOW_INSECURE_REMOTE_AUTH=true"
        )
    logger.warning(
        "DANGER: OPENBRIDGE_ALLOW_INSECURE_REMOTE_AUTH=true permits remote "
        "requests with authentication disabled or server-principal fallback. "
        "Use this only for a deliberately isolated single-tenant deployment."
    )
