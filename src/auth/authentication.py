"""Authentication-mode configuration for FastMCP-native providers."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from src.utils.runtime_security import env_flag

logger = logging.getLogger(__name__)

_VALID_AUTH_MODES = {"refresh_token", "oauth_proxy"}


@dataclass
class AuthConfig:
    """Authentication settings used while constructing the MCP server."""

    enabled: bool = True
    auth_mode: str = "refresh_token"


def _parse_auth_mode(raw: str | None) -> str:
    """Return a supported mode, preserving the compatibility default."""
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
    """Read native FastMCP authentication configuration from the environment."""
    return AuthConfig(
        enabled=env_flag("AUTH_ENABLED", default=True),
        auth_mode=_parse_auth_mode(os.getenv("OPENBRIDGE_AUTH_MODE")),
    )


__all__ = ["AuthConfig", "create_openbridge_config"]
