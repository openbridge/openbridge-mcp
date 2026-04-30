"""End-to-end smoke for the Code Mode tool surface.

Unlike ``test_code_mode.py`` (unit tests against ``src.server.code_mode``)
and ``test_mcp_server.py`` (FakeFastMCP-based wiring asserts), this file
builds a *real* :class:`fastmcp.FastMCP` via ``create_mcp_server`` and
asserts on what an MCP client would actually see when it calls
``list_tools()``.

The contract this file locks in:

* When ``CODE_MODE`` is unset or true, the only tools exposed are
  ``tags``, ``search``, ``get_schema``, ``execute``. Direct Openbridge
  tools are hidden behind the meta-tool transform.
* When ``CODE_MODE=false``, the direct catalog is exposed and a WARNING
  is logged so operators know they are off the recommended entry point.

These are integration tests; they import the experimental code-mode
extras transitively, so the suite needs ``fastmcp[code-mode]`` installed
(it is, via the project's runtime ``dependencies`` list).
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from src.server.mcp_server import create_mcp_server


CODE_MODE_TOOL_SURFACE = {"tags", "search", "get_schema", "execute"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Pin auth off and Docket to memory so tests stay offline."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("FASTMCP_DOCKET_URL", "memory://")
    # Sampling key absent → SQL tools skipped; that's fine for this suite.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FASTMCP_SAMPLING_API_KEY", raising=False)


def _list_tool_names(mcp) -> set[str]:
    return {t.name for t in asyncio.run(mcp.list_tools())}


# ---------------------------------------------------------------------------
# CODE_MODE=true (default) → only meta-tools surface
# ---------------------------------------------------------------------------


def test_code_mode_default_exposes_only_meta_tools(monkeypatch):
    """The recommended entry point: clients see search/get_schema/execute
    (plus optional tags) and nothing else. If a future change accidentally
    leaks the direct Openbridge catalog past the transform, this test
    fails with a clean diff."""
    monkeypatch.delenv("CODE_MODE", raising=False)
    mcp = create_mcp_server()
    assert _list_tool_names(mcp) == CODE_MODE_TOOL_SURFACE


def test_code_mode_explicit_true_exposes_only_meta_tools(monkeypatch):
    monkeypatch.setenv("CODE_MODE", "true")
    mcp = create_mcp_server()
    assert _list_tool_names(mcp) == CODE_MODE_TOOL_SURFACE


def test_code_mode_without_tags_exposes_three_meta_tools(monkeypatch):
    """``CODE_MODE_INCLUDE_TAGS=false`` drops the optional ``tags`` meta
    tool, leaving the minimal {search, get_schema, execute} surface."""
    monkeypatch.setenv("CODE_MODE", "true")
    monkeypatch.setenv("CODE_MODE_INCLUDE_TAGS", "false")
    mcp = create_mcp_server()
    assert _list_tool_names(mcp) == {"search", "get_schema", "execute"}


# ---------------------------------------------------------------------------
# CODE_MODE=false → direct catalog and warn-log
# ---------------------------------------------------------------------------


def test_code_mode_disabled_exposes_direct_catalog(monkeypatch):
    """When operators opt out, the full direct tool catalog is visible.
    ``get_capabilities`` is the only universally-present tool we can
    pin on (others are gated behind FASTMCP_SAMPLING_API_KEY); use it as
    a sentinel that confirms direct registration happened."""
    monkeypatch.setenv("CODE_MODE", "false")
    mcp = create_mcp_server()
    names = _list_tool_names(mcp)
    # Direct catalog markers — all from the Openbridge tool set.
    assert "get_capabilities" in names
    assert "get_jobs" in names
    assert "search_products" in names
    # And the meta-tool names must NOT be present.
    assert names.isdisjoint(CODE_MODE_TOOL_SURFACE)


def test_code_mode_disabled_emits_warn_log(monkeypatch, caplog):
    """An operator who turns off Code Mode should see a WARNING that
    flags they've left the recommended entry point. Locking this on
    INFO would let it get filtered out of production log streams; the
    warn level makes it discoverable."""
    monkeypatch.setenv("CODE_MODE", "false")
    with caplog.at_level(logging.WARNING, logger="mcp_query_execution.mcp_server"):
        create_mcp_server()
    warning_records = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING
        and "Code mode disabled" in rec.getMessage()
    ]
    assert warning_records, (
        "Expected a WARNING log when CODE_MODE=false; got: "
        + ", ".join(rec.getMessage() for rec in caplog.records)
    )
    assert "recommended primary entry point" in warning_records[0].getMessage()
