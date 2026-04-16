from src.server.tools import capabilities
from src.server.tools.tool_manifest import TOOL_MANIFEST


def test_build_capabilities_marks_query_tools_disabled_without_sampling_key(monkeypatch):
    monkeypatch.delenv("FASTMCP_SAMPLING_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENBRIDGE_ENABLE_LLM_VALIDATION", raising=False)
    monkeypatch.setenv("CODE_MODE", "false")

    result = capabilities.build_capabilities()
    validate_tool = next(tool for tool in result["tools"] if tool["name"] == "validate_query")
    execute_tool = next(tool for tool in result["tools"] if tool["name"] == "execute_query")

    assert result["runtime"]["sampling_key_present"] is False
    assert result["runtime"]["llm_validation_enabled"] is False
    assert result["runtime"]["code_mode_enabled"] is False
    assert validate_tool["enabled"] is False
    assert validate_tool["disabled_reason"] == "missing_sampling_key"
    assert execute_tool["enabled"] is False


def test_build_capabilities_marks_query_tools_enabled_with_sampling_key(monkeypatch):
    monkeypatch.setenv("FASTMCP_SAMPLING_API_KEY", "test-key")
    monkeypatch.setenv("OPENBRIDGE_ENABLE_LLM_VALIDATION", "true")
    monkeypatch.delenv("CODE_MODE", raising=False)

    result = capabilities.build_capabilities()
    validate_tool = next(tool for tool in result["tools"] if tool["name"] == "validate_query")
    execute_tool = next(tool for tool in result["tools"] if tool["name"] == "execute_query")

    assert result["runtime"]["sampling_key_present"] is True
    assert result["runtime"]["llm_validation_enabled"] is True
    assert result["runtime"]["code_mode_enabled"] is True
    assert validate_tool["enabled"] is True
    assert validate_tool["llm_validation_enabled"] is True
    assert execute_tool["enabled"] is True


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

    result = capabilities.build_capabilities()

    total = len(TOOL_MANIFEST)
    expected_disabled = len(SAMPLING_GATED_TOOLS)
    assert result["summary"]["total_tools_declared"] == total
    assert result["summary"]["disabled_tools"] == expected_disabled
    assert result["summary"]["enabled_tools"] == total - expected_disabled


def test_build_capabilities_summary_counts_with_sampling_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("CODE_MODE", "false")

    result = capabilities.build_capabilities()

    total = len(TOOL_MANIFEST)
    assert result["summary"]["total_tools_declared"] == total
    assert result["summary"]["enabled_tools"] == total
    assert result["summary"]["disabled_tools"] == 0


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
    assert by_name["validate_query"]["enabled"] is False
    assert by_name["execute_query"]["enabled"] is False
    assert by_name["get_subscriptions"]["enabled"] is True
    assert result["summary"]["enabled_tools"] == len(registered)
    assert result["summary"]["disabled_tools"] == len(SAMPLING_GATED_TOOLS)


def test_build_capabilities_lists_every_manifest_tool():
    """Defensive: build_capabilities must emit one entry per manifest key
    — catches accidentally filtering out a tool in the loop."""
    result = capabilities.build_capabilities()
    emitted = {t["name"] for t in result["tools"]}
    assert emitted == set(TOOL_MANIFEST.keys())
