"""Tests for the skills meta-tool wrapper (``list_skills`` / ``read_skill``).

Why these tools exist: FastMCP exposes skills as MCP resources via the
``resources/*`` protocol channel. Some MCP hosts (Claude.ai's, as of
2026-05-01) only route ``tools/*`` traffic into the assistant context,
so a skill published via ``SkillsDirectoryProvider`` is invisible from
inside Code Mode's ``call_tool()`` sandbox even though it's correctly
on the wire and reachable to MCP Inspector / mcp-cli / FastMCP
``Client``. The two meta-tools bridge the resource channel back into
the tool channel: ``list_skills()`` enumerates skill resources via the
in-process ``mcp.list_resources()`` call, and ``read_skill(uri)``
returns the file body via ``mcp.read_resource(uri)``.

These are pure pass-throughs — no parsing, no transformation, no
caching. The values come from the same SkillsDirectoryProvider the
resource API queries, so any change to skills/* on disk is reflected
on the next call.
"""

from __future__ import annotations

import asyncio
import pytest

from src.server.mcp_server import create_mcp_server
from src.server.tools import skills_meta


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("FASTMCP_DOCKET_URL", "memory://")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FASTMCP_SAMPLING_API_KEY", raising=False)


class _StubCtx:
    """Just enough Context for the meta-tools to call ``ctx.fastmcp``."""

    def __init__(self, fastmcp):
        self.fastmcp = fastmcp


# ---------------------------------------------------------------------------
# list_skills
# ---------------------------------------------------------------------------


def test_list_skills_returns_skill_uris():
    """``list_skills()`` enumerates every ``skill://`` resource the
    server publishes. Today: at least the bundled openbridge-mcp skill
    (SKILL.md, _manifest, references/*, evals/, mcp-servers.json — 9
    files post the AppleDouble fix in v0.3.3)."""
    mcp = create_mcp_server()
    ctx = _StubCtx(mcp)

    result = asyncio.run(skills_meta.list_skills(ctx=ctx))

    assert isinstance(result, list), f"expected list, got {type(result).__name__}: {result!r}"
    skill_uris = [item["uri"] for item in result]
    assert any(uri.startswith("skill://openbridge-mcp/") for uri in skill_uris), (
        f"expected at least one skill://openbridge-mcp/ URI; got {skill_uris!r}"
    )
    # Ensure the canonical SKILL.md and _manifest are both present.
    assert "skill://openbridge-mcp/SKILL.md" in skill_uris
    assert "skill://openbridge-mcp/_manifest" in skill_uris


def test_list_skills_only_returns_skill_scheme():
    """Even if FastMCP later adds non-skill resources (e.g. a custom
    provider exposing ``data://...``), ``list_skills`` MUST only emit
    ``skill://`` URIs. The assistant uses this output to know which
    URIs are valid inputs to ``read_skill``."""
    mcp = create_mcp_server()
    ctx = _StubCtx(mcp)

    result = asyncio.run(skills_meta.list_skills(ctx=ctx))

    for item in result:
        assert item["uri"].startswith("skill://"), (
            f"non-skill URI leaked into list_skills output: {item!r}"
        )


def test_list_skills_entries_have_expected_keys():
    """Each entry has a stable shape so the assistant doesn't have to
    branch on missing fields. Required keys: uri, name, description,
    mime_type."""
    mcp = create_mcp_server()
    ctx = _StubCtx(mcp)

    result = asyncio.run(skills_meta.list_skills(ctx=ctx))

    assert len(result) >= 1
    expected_keys = {"uri", "name", "description", "mime_type"}
    for item in result:
        assert expected_keys.issubset(item.keys()), (
            f"missing keys in list_skills entry: {sorted(expected_keys - set(item.keys()))} "
            f"(got {sorted(item.keys())})"
        )


# ---------------------------------------------------------------------------
# read_skill
# ---------------------------------------------------------------------------


def test_read_skill_returns_skill_md_content():
    """The canonical happy path: read SKILL.md and confirm the body
    contains the frontmatter description's shibboleth."""
    mcp = create_mcp_server()
    ctx = _StubCtx(mcp)

    result = asyncio.run(
        skills_meta.read_skill(uri="skill://openbridge-mcp/SKILL.md", ctx=ctx)
    )

    assert isinstance(result, dict)
    assert result.get("uri") == "skill://openbridge-mcp/SKILL.md"
    assert "Drive the Openbridge platform" in result.get("content", ""), (
        f"SKILL.md body did not contain shibboleth. First 200 chars: "
        f"{result.get('content', '')[:200]!r}"
    )


def test_read_skill_returns_supporting_doc():
    """Reference docs are addressable by their full URI — exercises the
    ``supporting_files='resources'`` configuration that surfaces them
    individually."""
    mcp = create_mcp_server()
    ctx = _StubCtx(mcp)

    result = asyncio.run(
        skills_meta.read_skill(
            uri="skill://openbridge-mcp/references/workflows.md",
            ctx=ctx,
        )
    )

    assert "End-to-end recipes" in result.get("content", "")


def test_read_skill_rejects_non_skill_uri():
    """A URI that isn't ``skill://...`` is a contract violation.
    Return a v1 envelope with ``mcp_input_validation`` rather than
    blindly proxying to ``read_resource()`` (which could reach into
    non-skill providers)."""
    mcp = create_mcp_server()
    ctx = _StubCtx(mcp)

    result = asyncio.run(
        skills_meta.read_skill(uri="data://something/else", ctx=ctx)
    )

    assert result.get("_envelope_version") == 1
    assert result.get("error_kind") == "mcp_input_validation"
    assert result.get("tool") == "read_skill"


def test_read_skill_unknown_uri_returns_envelope():
    """Unknown skill URI returns an envelope, not a raise — keeps the
    sandbox-callable contract: every error is a returned dict, never
    an unhandled exception."""
    mcp = create_mcp_server()
    ctx = _StubCtx(mcp)

    result = asyncio.run(
        skills_meta.read_skill(
            uri="skill://does-not-exist/SKILL.md",
            ctx=ctx,
        )
    )

    assert result.get("_envelope_version") == 1
    assert result.get("error_kind") in {"mcp_input_validation", "internal_error"}
