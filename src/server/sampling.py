"""Direct OpenAI sampling utilities for SQL query validation."""

from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

from src.utils.logging import get_logger

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency
    OpenAI = None  # type: ignore[assignment]

logger = get_logger("sampling")


def create_openai_client() -> Optional[Any]:
    """Create the OpenAI client used for opt-in SQL validation."""
    if OpenAI is None:
        logger.info("OpenAI validation disabled: openai dependency not available")
        return None

    api_key = os.getenv("FASTMCP_SAMPLING_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.info("OpenAI validation disabled: no API key configured")
        return None

    base_url = os.getenv("FASTMCP_SAMPLING_BASE_URL")
    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    return OpenAI(**client_kwargs)


async def sample_text(
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0,
    max_tokens: int = 400,
) -> str:
    """Return text from the OpenAI Responses API without blocking the event loop."""
    client = create_openai_client()
    if client is None:
        raise RuntimeError("OpenAI client is unavailable")

    model = os.getenv("FASTMCP_SAMPLING_MODEL", "gpt-4o-mini")
    response = await asyncio.to_thread(
        client.responses.create,
        model=model,
        instructions=system_prompt,
        input=user_prompt,
        temperature=temperature,
        max_output_tokens=max_tokens,
    )
    output_text = response.output_text
    if not isinstance(output_text, str):
        raise RuntimeError("OpenAI response did not contain text output")
    return output_text.strip()
