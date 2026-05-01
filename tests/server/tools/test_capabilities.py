from src.server.tools import capabilities
from src.server.tools.tool_manifest import TOOL_MANIFEST


def test_build_capabilities_marks_query_tools_disabled_without_sampling_key(monkeypatch):
    monkeypatch.delenv("FASTMCP_SAMPLING_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENBRIDGE_ENABLE_LLM_VALIDATION", raising=False)
    monkeypatch.setenv("CODE_MODE", "false")
    registered = set(TOOL_MANIFEST.keys()) - {"validate_query", "execute_query"}

    result = capabilities.build_capabilities(registered)
    names = {tool["name"] for tool in result["tools"]}

    assert result["runtime"]["sampling_key_present"] is False
    assert result["runtime"]["llm_validation_enabled"] is False
    assert result["runtime"]["code_mode_enabled"] is False
    assert "validate_query" not in names
    assert "execute_query" not in names
    assert set(result["not_installed"]) == {"validate_query", "execute_query"}


def test_build_capabilities_marks_query_tools_enabled_with_sampling_key(monkeypatch):
    monkeypatch.setenv("FASTMCP_SAMPLING_API_KEY", "test-key")
    monkeypatch.setenv("OPENBRIDGE_ENABLE_LLM_VALIDATION", "true")
    monkeypatch.delenv("CODE_MODE", raising=False)
    registered = set(TOOL_MANIFEST.keys())

    result = capabilities.build_capabilities(registered)
    validate_tool = next(tool for tool in result["tools"] if tool["name"] == "validate_query")
    execute_tool = next(tool for tool in result["tools"] if tool["name"] == "execute_query")

    assert result["runtime"]["sampling_key_present"] is True
    assert result["runtime"]["llm_validation_enabled"] is True
    assert result["runtime"]["code_mode_enabled"] is True
    assert validate_tool["enabled"] is True
    assert validate_tool["llm_validation_enabled"] is True
    assert execute_tool["enabled"] is True
    assert result["not_installed"] == []


def test_build_capabilities_marks_execute_query_not_installed_when_disabled(monkeypatch):
    monkeypatch.setenv("FASTMCP_SAMPLING_API_KEY", "test-key")
    monkeypatch.setenv("OPENBRIDGE_ENABLE_QUERY_EXECUTION", "false")
    registered = set(TOOL_MANIFEST.keys()) - {"execute_query"}

    result = capabilities.build_capabilities(registered)
    names = {tool["name"] for tool in result["tools"]}
    assert "validate_query" in names
    assert "execute_query" not in names
    assert "execute_query" in result["not_installed"]


# ---------------------------------------------------------------------------
# Phase 3d — gate-state / summary-count invariants
# ---------------------------------------------------------------------------


SAMPLING_GATED_TOOLS = frozenset({"validate_query", "execute_query"})


def test_build_capabilities_summary_counts_without_sampling_key(monkeypatch):
    """The summary must reflect that exactly the sampling-gated tools are
    disabled when no API key is present — not a hand-coded number."""
    monkeypatch.delenv("FASTMCP_SAMPLING_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CODE_MODE", "false")
    registered = set(TOOL_MANIFEST.keys()) - SAMPLING_GATED_TOOLS

    result = capabilities.build_capabilities(registered)

    total = len(TOOL_MANIFEST)
    expected_not_installed = len(SAMPLING_GATED_TOOLS)
    summary = result["summary"]
    assert summary["total_tools_declared"] == total
    assert summary["disabled_tools"] == 0
    assert summary["not_installed_tools"] == expected_not_installed
    assert summary["enabled_tools"] == total - expected_not_installed
    # Invariant: total == enabled + disabled + not_installed.
    assert (
        summary["total_tools_declared"]
        == summary["enabled_tools"] + summary["disabled_tools"] + summary["not_installed_tools"]
    )


def test_build_capabilities_summary_counts_with_sampling_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("CODE_MODE", "false")
    registered = set(TOOL_MANIFEST.keys())

    result = capabilities.build_capabilities(registered)

    total = len(TOOL_MANIFEST)
    summary = result["summary"]
    assert summary["total_tools_declared"] == total
    assert summary["enabled_tools"] == total
    assert summary["disabled_tools"] == 0
    assert summary["not_installed_tools"] == 0
    assert (
        summary["total_tools_declared"]
        == summary["enabled_tools"] + summary["disabled_tools"] + summary["not_installed_tools"]
    )


def test_build_capabilities_respects_registered_tool_names(monkeypatch):
    """When the server passes an explicit registered_tool_names set, the
    'enabled' flag must come from that set, not from env-var inference."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")  # would normally enable

    # Simulate a server that happens to have NOT registered the sampling
    # tools (e.g. a test harness with disabled code paths). The capabilities
    # output must reflect registration truth, not env truth.
    registered = set(TOOL_MANIFEST.keys()) - SAMPLING_GATED_TOOLS
    result = capabilities.build_capabilities(registered_tool_names=registered)

    by_name = {t["name"]: t for t in result["tools"]}
    assert "validate_query" not in by_name
    assert "execute_query" not in by_name
    assert by_name["get_subscriptions"]["enabled"] is True
    summary = result["summary"]
    assert summary["enabled_tools"] == len(registered)
    assert summary["disabled_tools"] == 0
    assert summary["not_installed_tools"] == len(SAMPLING_GATED_TOOLS)
    assert (
        summary["total_tools_declared"]
        == summary["enabled_tools"] + summary["disabled_tools"] + summary["not_installed_tools"]
    )
    assert set(result["not_installed"]) == SAMPLING_GATED_TOOLS


def test_build_capabilities_lists_every_manifest_tool():
    """Defensive: build_capabilities emits only the registered subset."""
    registered = set(TOOL_MANIFEST) - SAMPLING_GATED_TOOLS
    result = capabilities.build_capabilities(registered)
    emitted = {t["name"] for t in result["tools"]}
    assert emitted == registered
