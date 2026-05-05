"""Meta-tools that bridge the MCP resource channel into the tool channel.

FastMCP's ``SkillsDirectoryProvider`` (wired in
``src.server.mcp_server``) publishes the bundled
``skills/openbridge-mcp/`` skill as MCP resources with URIs like
``skill://openbridge-mcp/SKILL.md``. That's the canonical mechanism
documented at https://gofastmcp.com/servers/providers/skills.

Some MCP hosts only route ``tools/*`` traffic into their assistant
context — Claude.ai's MCP host as of 2026-05-01 is the verified example.
The skill is published, valid, and reachable via the FastMCP ``Client``
SDK / mcp-cli / MCP Inspector, but a Code Mode-sandboxed assistant on
Claude.ai cannot enumerate it through ``call_tool()`` because there is
no ``call_tool("list_resources", ...)``-style helper in the sandbox.

These two tools paper over the gap:

- ``list_skills()`` enumerates skill resources via the in-process
  ``ctx.fastmcp.list_resources()`` call. The host's tool filter doesn't
  apply because the call never crosses the wire — it's a Python method
  call on the running FastMCP instance.
- ``read_skill(uri)`` returns the file body via
  ``ctx.fastmcp.read_resource(uri)``.

Both are pure pass-throughs. No caching, no transformation. If a
skill changes on disk, the next call sees the change (subject to
the provider's ``reload`` setting; we ship with ``reload=False``).

If the host you're targeting natively surfaces resources to the
assistant (Claude Code, Inspector, FastMCP ``Client`` SDK), prefer
the resource API directly — it's more idiomatic and you'd skip the
hop.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastmcp.server.context import Context

from src.utils.envelope import make_error
from src.utils.logging import get_logger


logger = get_logger("skills_meta")


_SKILL_SCHEME = "skill://"


def _no_context_envelope(tool: str) -> Dict[str, Any]:
    return make_error(
        tool=tool,
        error_kind="internal_error",
        summary="Tool invoked outside a FastMCP request context",
        error_code="MISSING_CONTEXT",
        retryable=False,
        details=[{
            "path": "ctx",
            "issue": "ctx.fastmcp is required to access the resource registry",
            "received_type": "NoneType",
        }],
    )


async def list_skills(ctx: Optional[Context] = None) -> List[Dict[str, Any]]:
    """Enumerate skill resources published by this server.

    Returns a list of ``{uri, name, description, mime_type}`` dicts —
    one entry per skill resource (main file, manifest, supporting
    docs). Non-skill resources (any URI not starting with
    ``skill://``) are filtered out so the assistant has a single,
    well-typed input shape for ``read_skill``.

    Args:
        ctx: FastMCP request context. Required — the underlying
            ``list_resources()`` lives on the FastMCP server instance
            reachable via ``ctx.fastmcp``.

    Returns:
        List of skill resource descriptors. Empty list when no skills
        are registered (e.g., a stripped install where the
        ``SkillsDirectoryProvider`` was skipped).
    """
    if ctx is None or getattr(ctx, "fastmcp", None) is None:
        # Empty list rather than envelope here — "no skills" is a valid
        # answer to "what skills are there", not an error condition.
        # The envelope path is reserved for actual failures.
        logger.warning("list_skills called without ctx.fastmcp; returning empty list")
        return []

    resources = await ctx.fastmcp.list_resources()
    skills: List[Dict[str, Any]] = []
    for resource in resources:
        uri_str = str(getattr(resource, "uri", ""))
        if not uri_str.startswith(_SKILL_SCHEME):
            continue
        skills.append({
            "uri": uri_str,
            "name": getattr(resource, "name", "") or uri_str,
            "description": getattr(resource, "description", None) or "",
            "mime_type": getattr(resource, "mime_type", None) or "text/plain",
        })
    return skills


async def read_skill(
    uri: str,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """Read the body of a skill resource by URI.

    Args:
        uri: Full ``skill://<skill-name>/<file-path>`` URI as returned
            by ``list_skills()``. Other schemes are rejected with a v1
            envelope so this tool can't be repurposed to read
            arbitrary non-skill resources.
        ctx: FastMCP request context. Required for resource access.

    Returns:
        On success: ``{uri, content, mime_type}``.
        On failure: v1 error envelope (mcp_input_validation for bad
        URI shape, internal_error for missing ctx, mcp_input_validation
        with SKILL_NOT_FOUND-style error_code for unknown URIs).
    """
    if not isinstance(uri, str) or not uri.startswith(_SKILL_SCHEME):
        return make_error(
            tool="read_skill",
            error_kind="mcp_input_validation",
            summary=f"URI must start with {_SKILL_SCHEME!r}",
            error_code="INVALID_SKILL_URI",
            retryable=False,
            details=[{
                "path": "uri",
                "issue": f"URI did not start with {_SKILL_SCHEME!r}",
                "received_type": type(uri).__name__,
            }],
            hints=[
                "Call list_skills() to see the URIs this server publishes.",
                f"Skill URIs always start with {_SKILL_SCHEME!r}.",
            ],
        )

    if ctx is None or getattr(ctx, "fastmcp", None) is None:
        return _no_context_envelope("read_skill")

    try:
        result = await ctx.fastmcp.read_resource(uri)
    except Exception as exc:
        # Lookup miss, file removed between list_skills() and
        # read_skill(), or any other read failure. Wrap as envelope —
        # callers should treat this as "URI no longer addressable"
        # and refresh via list_skills().
        logger.info("read_skill failed for uri=%s: %s", uri, exc)
        return make_error(
            tool="read_skill",
            error_kind="mcp_input_validation",
            summary=f"Skill resource not found: {uri}",
            error_code="SKILL_NOT_FOUND",
            retryable=False,
            details=[{
                "path": "uri",
                "issue": str(exc) or "Resource lookup failed",
                "received_type": type(exc).__name__,
            }],
            hints=[
                "Re-run list_skills() to see currently registered URIs.",
                "Reload may be off — check that the skill is actually present in skills/.",
            ],
        )

    contents = getattr(result, "contents", None)
    if not contents:
        return make_error(
            tool="read_skill",
            error_kind="internal_error",
            summary=f"Skill resource returned empty content envelope: {uri}",
            error_code="EMPTY_RESOURCE",
            retryable=False,
            details=[{
                "path": "uri",
                "issue": "ctx.fastmcp.read_resource returned no content blocks",
                "received_type": type(result).__name__,
            }],
        )

    block = contents[0]
    # FastMCP resource blocks expose the body on .content (skills
    # provider) or .text (older shape). Try both for forward-compat.
    body = getattr(block, "content", None)
    if body is None:
        body = getattr(block, "text", None)
    if body is None:
        body = ""

    return {
        "uri": uri,
        "content": body,
        "mime_type": getattr(block, "mime_type", None) or "text/plain",
    }


__all__ = ["list_skills", "read_skill"]
