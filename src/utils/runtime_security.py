"""Shared startup policy for security-sensitive runtime settings."""

from ipaddress import ip_address
import os

from src.utils.logging import get_logger

logger = get_logger("runtime_security")

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
KNOWN_PLACEHOLDERS = {
    "your-strong-secret-here",
    "your-strong-stable-secret-here",
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


def validate_secret(name: str, value: str, *, minimum_bytes: int = 32) -> None:
    """Reject documented placeholders and undersized secret material."""
    normalized = value.strip()
    if normalized.lower() in KNOWN_PLACEHOLDERS:
        raise RuntimeError(f"{name} uses a known placeholder")
    if len(normalized.encode("utf-8")) < minimum_bytes:
        raise RuntimeError(f"{name} must contain at least {minimum_bytes} bytes")


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
    auth_enabled = env_flag("AUTH_ENABLED", default=True)
    auth_mode = os.getenv("OPENBRIDGE_AUTH_MODE", "refresh_token").strip().lower()
    if auth_mode == "oauth_proxy" and not is_loopback_host(host):
        validate_secret("MCP_JWT_SIGNING_KEY", os.getenv("MCP_JWT_SIGNING_KEY", ""))
    if path_tokens_enabled():
        if not auth_enabled:
            raise RuntimeError(
                "MCP path tokens require AUTH_ENABLED=true so injected "
                "credentials reach the native verifier"
            )
        if auth_mode != "refresh_token":
            raise RuntimeError(
                "MCP path tokens are supported only in OPENBRIDGE_AUTH_MODE=refresh_token"
            )
        secret = os.getenv("MCP_PATH_TOKEN_SECRET", "").strip()
        validate_secret("MCP_PATH_TOKEN_SECRET", secret)
        for name in ("MCP_PATH_TOKEN_TTL_DAYS", "MCP_PATH_TOKEN_MAX_AGE_DAYS"):
            raw = os.getenv(name, "7")
            try:
                days = int(raw)
            except ValueError as exc:
                raise RuntimeError(f"{name} must be an integer from 1 through 7") from exc
            if not 1 <= days <= 7:
                raise RuntimeError(f"{name} must be an integer from 1 through 7")

    unsafe_override = env_flag(
        "OPENBRIDGE_ALLOW_INSECURE_REMOTE_AUTH",
        default=False,
    )
    unsafe_remote = not is_loopback_host(host) and not auth_enabled
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
