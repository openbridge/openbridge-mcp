"""FastMCP middleware that maps uncaught exceptions to v1 error envelopes."""

from __future__ import annotations

import json
from typing import Any

from fastmcp.exceptions import NotFoundError, ToolError, ValidationError as FastMCPValidationError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from pydantic import ValidationError as PydanticValidationError

from src.utils.envelope import make_error
from src.utils.logging import get_logger

logger = get_logger("error_envelope_middleware")


def _tool_name_from_context(context: MiddlewareContext) -> str:
    message = getattr(context, "message", None)
    return getattr(message, "name", None) or "unknown_tool"


def _pydantic_details(exc: PydanticValidationError) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for item in exc.errors():
        path = ".".join(str(part) for part in item.get("loc", ()))
        raw_input = item.get("input", None)
        if raw_input is None and "input" not in item:
            received_type = "unknown"
        else:
            received_type = type(raw_input).__name__
        details.append({
            "path": path,
            "issue": item.get("msg", "Invalid input"),
            "received_type": received_type,
        })
    return details or [{
        "path": "",
        "issue": "Invalid input",
        "received_type": "unknown",
    }]


def _validation_envelope(tool: str, summary: str, details: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return make_error(
        tool=tool,
        error_kind="mcp_input_validation",
        summary=summary,
        error_code="INPUT_VALIDATION_FAILED",
        retryable=False,
        details=details,
    )


class ErrorEnvelopeMiddleware(Middleware):
    """Convert uncaught tool/runtime exceptions to contract-compliant envelopes."""

    async def on_request(self, context: MiddlewareContext, call_next):
        try:
            return await call_next(context)
        except NotFoundError as exc:
            tool = _tool_name_from_context(context)
            envelope = make_error(
                tool=tool,
                error_kind="tool_not_found",
                summary=f"Tool '{tool}' is not registered on this server",
                error_code="TOOL_NOT_FOUND",
                retryable=False,
            )
            raise ToolError(json.dumps(envelope)) from exc

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        tool = _tool_name_from_context(context)
        try:
            return await call_next(context)
        except ToolError:
            # Preserve explicit tool errors as authored.
            raise
        except PydanticValidationError as exc:
            envelope = _validation_envelope(
                tool=tool,
                summary="Tool input validation failed",
                details=_pydantic_details(exc),
            )
            raise ToolError(json.dumps(envelope)) from exc
        except FastMCPValidationError as exc:
            envelope = _validation_envelope(
                tool=tool,
                summary="Tool input validation failed",
                details=[{
                    "path": "",
                    "issue": str(exc),
                    "received_type": type(exc).__name__,
                }],
            )
            raise ToolError(json.dumps(envelope)) from exc
        except KeyError as exc:
            logger.exception("KeyError in tool %s", tool)
            envelope = make_error(
                tool=tool,
                error_kind="internal_error",
                summary="Internal server error",
                error_code="INTERNAL_ERROR",
                retryable=False,
                details=[{
                    "path": "",
                    "issue": "Unexpected missing key while executing tool",
                    "received_type": "KeyError",
                }],
            )
            raise ToolError(json.dumps(envelope)) from exc
        except Exception as exc:
            logger.exception("Unhandled exception in tool %s", tool)
            envelope = make_error(
                tool=tool,
                error_kind="internal_error",
                summary="Internal server error",
                error_code="INTERNAL_ERROR",
                retryable=False,
                details=[{
                    "path": "",
                    "issue": "Unhandled exception while executing tool",
                    "received_type": type(exc).__name__,
                }],
            )
            raise ToolError(json.dumps(envelope)) from exc
