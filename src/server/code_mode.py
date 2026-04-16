"""Code mode integration for Openbridge MCP.

When enabled, Code Mode replaces the exposed tool catalog with lightweight
meta-tools (for discovery + execute).
"""

from __future__ import annotations

import os


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def is_code_mode_enabled() -> bool:
    """Return True when code mode should be active."""
    return _env_bool("CODE_MODE", True)


def create_code_mode_transform():
    """Create a configured CodeMode transform.

    Raises:
        ImportError: if FastMCP code-mode extras are unavailable.
    """
    try:
        from fastmcp.experimental.transforms.code_mode import (
            CodeMode,
            GetSchemas,
            GetTags,
            MontySandboxProvider,
            Search,
        )
    except ImportError as exc:
        raise ImportError(
            "Code mode requires fastmcp experimental transforms. "
            "Install with: pip install 'fastmcp[code-mode]>=3.1.0'"
        ) from exc

    include_tags = _env_bool("CODE_MODE_INCLUDE_TAGS", True)
    max_duration_secs = float(os.getenv("CODE_MODE_MAX_DURATION_SECS", "30"))
    max_memory = int(os.getenv("CODE_MODE_MAX_MEMORY", "50000000"))

    discovery_tools = []
    if include_tags:
        discovery_tools.append(GetTags())
    discovery_tools.extend([Search(), GetSchemas()])

    sandbox = MontySandboxProvider(
        limits={
            "max_duration_secs": max_duration_secs,
            "max_memory": max_memory,
        }
    )

    return CodeMode(
        sandbox_provider=sandbox,
        discovery_tools=discovery_tools,
    )
