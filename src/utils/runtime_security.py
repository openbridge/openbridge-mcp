"""Shared startup policy for security-sensitive runtime settings."""

from ipaddress import ip_address
import os

from src.utils.logging import get_logger

logger = get_logger("runtime_security")

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_PATH_TOKEN_PLACEHOLDERS = {
    "your-strong-secret-here",
    "your-32-byte-hex-secret-here",
}


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


def path_tokens_enabled() -> bool:
    """Return True only when legacy URL path tokens are explicitly enabled."""
    return env_flag("MCP_PATH_TOKEN_ENABLED", default=False)


def positive_int_env(name: str, *, default: int) -> int:
    """Read a strictly positive integer or fail startup with a clear error."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be a positive integer")
    return value


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
    positive_int_env("OPENBRIDGE_AUTH_EXCHANGE_CONCURRENCY", default=8)
    if path_tokens_enabled():
        auth_mode = os.getenv("OPENBRIDGE_AUTH_MODE", "refresh_token").strip().lower()
        if auth_mode != "refresh_token":
            raise RuntimeError(
                "MCP path tokens are supported only in OPENBRIDGE_AUTH_MODE=refresh_token"
            )
        secret = os.getenv("MCP_PATH_TOKEN_SECRET", "").strip()
        if secret in _PATH_TOKEN_PLACEHOLDERS or len(secret.encode()) < 32:
            raise RuntimeError(
                "MCP_PATH_TOKEN_SECRET must contain at least 32 bytes of "
                "non-placeholder secret material when path tokens are enabled"
            )
        for name in ("MCP_PATH_TOKEN_TTL_DAYS", "MCP_PATH_TOKEN_MAX_AGE_DAYS"):
            raw = os.getenv(name, "7")
            try:
                days = int(raw)
            except ValueError as exc:
                raise RuntimeError(f"{name} must be an integer from 1 through 7") from exc
            if not 1 <= days <= 7:
                raise RuntimeError(f"{name} must be an integer from 1 through 7")

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
