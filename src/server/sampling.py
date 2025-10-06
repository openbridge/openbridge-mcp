"""Sampling handler utilities for the Openbridge MCP server.

This module centralizes creation of the global FastMCP sampling handler so the
server can consistently fall back to a known LLM when clients do not implement
sampling themselves.
"""

from __future__ import annotations

import os
from typing import Optional

from src.utils.logging import get_logger

try:
    from fastmcp.experimental.sampling.handlers.openai import (
        OpenAISamplingHandler,
    )
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency
    OpenAISamplingHandler = None  # type: ignore[assignment]
    OpenAI = None  # type: ignore[assignment]

logger = get_logger("sampling")


def create_sampling_handler() -> Optional[object]:
    """Create a sampling handler for FastMCP if dependencies are available.

    Returns:
        Optional[object]: An initialized sampling handler or ``None`` when
        sampling should be disabled (missing dependencies or configuration).
    """
    if OpenAISamplingHandler is None or OpenAI is None:
        logger.info("FastMCP sampling disabled: openai dependency not available")
        return None

    api_key = os.getenv("FASTMCP_SAMPLING_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.info("FastMCP sampling disabled: no API key configured")
        return None

    model = os.getenv("FASTMCP_SAMPLING_MODEL", "gpt-4o-mini")
    base_url = os.getenv("FASTMCP_SAMPLING_BASE_URL")

    try:
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = OpenAI(**client_kwargs)
        handler = OpenAISamplingHandler(default_model=model, client=client)
        logger.debug(
            "Enabled FastMCP sampling fallback handler using model '%s'", model
        )
        return handler
    except Exception as exc:  # pragma: no cover - runtime safety
        logger.warning("FastMCP sampling disabled: failed to initialize handler (%s)", exc)
        return None
