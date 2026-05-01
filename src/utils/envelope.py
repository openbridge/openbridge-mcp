"""Helpers for building v1 error envelopes defined in CONTRACT.md."""

from __future__ import annotations

from typing import Any, Literal

ErrorKind = Literal[
    "mcp_input_validation",
    "tool_not_found",
    "sp_api_http",
    "ads_api_http",
    "sp_api_client",
    "ads_api_client",
    "auth_error",
    "rate_limited",
    "sandbox_runtime",
    "internal_error",
]

ENVELOPE_VERSION = 1


def _default_details(summary: str) -> list[dict[str, Any]]:
    return [{
        "path": "",
        "issue": summary,
        "received_type": "unknown",
    }]


def make_error(
    *,
    tool: str,
    error_kind: ErrorKind,
    summary: str,
    error_code: str,
    retryable: bool,
    details: list[dict[str, Any]] | None = None,
    hints: list[str] | None = None,
    examples: list[Any] | None = None,
    meta: dict[str, Any] | None = None,
    legacy_error_kind: str | None = None,
) -> dict[str, Any]:
    """Build a v1-conforming error envelope."""
    envelope: dict[str, Any] = {
        "error_kind": error_kind,
        "tool": tool,
        "summary": summary,
        "details": details if details is not None else _default_details(summary),
        "hints": hints if hints is not None else [],
        "examples": examples if examples is not None else [],
        "error_code": error_code,
        "retryable": retryable,
        "_envelope_version": ENVELOPE_VERSION,
    }
    if meta is not None:
        envelope["_meta"] = meta
    if legacy_error_kind is not None:
        envelope["legacy_error_kind"] = legacy_error_kind
    return envelope


def not_found(
    *,
    tool: str,
    resource_type: str,
    resource_id: str | int,
    error_code: str,
) -> dict[str, Any]:
    """Common not-found envelope shape for ID lookups."""
    resource_type_label = resource_type.replace("_", " ")
    summary = f"{resource_type_label.title()} {resource_id} not found"
    return make_error(
        tool=tool,
        error_kind="mcp_input_validation",
        summary=summary,
        error_code=error_code,
        retryable=False,
        details=[{
            "path": f"{resource_type}.id",
            "issue": summary,
            "received_type": type(resource_id).__name__,
        }],
        hints=[f"Verify the {resource_type} ID belongs to the authenticated account."],
    )


def auth_error(
    *,
    tool: str,
    summary: str,
    hints: list[str] | None = None,
    details: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Common auth failure envelope shape."""
    return make_error(
        tool=tool,
        error_kind="auth_error",
        summary=summary,
        error_code="AUTHENTICATION_ERROR",
        retryable=False,
        hints=hints,
        details=details,
    )
