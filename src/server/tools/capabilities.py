import os
from typing import Any, Dict, List, Set

from src.server.code_mode import is_code_mode_enabled
from .tool_manifest import TOOL_MANIFEST

_SUPPORTED_ERROR_KINDS = [
    "mcp_input_validation",
    "tool_not_found",
    "auth_error",
    "rate_limited",
    "sandbox_runtime",
    "internal_error",
]


def _env_present(name: str) -> bool:
    value = os.getenv(name)
    return bool(value and value.strip())


def build_capabilities(registered_tool_names: Set[str]) -> Dict[str, Any]:
    """Build capability metadata for the current MCP runtime."""
    has_sampling_key = _env_present("FASTMCP_SAMPLING_API_KEY") or _env_present("OPENAI_API_KEY")
    llm_opt_in = os.getenv("OPENBRIDGE_ENABLE_LLM_VALIDATION", "false").lower() == "true"
    query_execution_enabled = os.getenv("OPENBRIDGE_ENABLE_QUERY_EXECUTION", "true").lower() == "true"

    tools: List[Dict[str, Any]] = []
    not_installed: List[str] = []
    for tool_name, meta in TOOL_MANIFEST.items():
        if tool_name not in registered_tool_names:
            not_installed.append(tool_name)
            continue

        tool: Dict[str, Any] = {
            "name": tool_name,
            "category": meta["category"],
            "enabled": True,
        }

        if tool_name == "validate_query":
            tool["requires_env"] = ["FASTMCP_SAMPLING_API_KEY or OPENAI_API_KEY"]
            tool["llm_opt_in_required"] = True
            tool["llm_validation_enabled"] = llm_opt_in
        elif tool_name == "execute_query":
            tool["requires_env"] = ["FASTMCP_SAMPLING_API_KEY or OPENAI_API_KEY"]
            tool["llm_opt_in_required"] = True
            tool["query_execution_enabled"] = query_execution_enabled

        tools.append(tool)

    # Invariant: total_tools_declared == enabled_tools + disabled_tools + not_installed_tools.
    # `disabled_tools` exists for forward-compat (a future feature-flag may
    # disable a registered tool at runtime); today it's always 0 because
    # registration is binary. `not_installed_tools` covers the case the
    # original v0.2.0 release missed: tools the manifest declares but the
    # current process did not register (e.g. sampling-gated query tools
    # without an API key).
    return {
        "summary": {
            "total_tools_declared": len(TOOL_MANIFEST),
            "enabled_tools": len(tools),
            "disabled_tools": 0,
            "not_installed_tools": len(not_installed),
        },
        "runtime": {
            "sampling_key_present": has_sampling_key,
            "llm_validation_enabled": llm_opt_in,
            "code_mode_enabled": is_code_mode_enabled(),
            "query_execution_enabled": query_execution_enabled,
        },
        "openbridge_envelope": {
            "contract_version": 1,
            "error_kinds": _SUPPORTED_ERROR_KINDS,
        },
        "not_installed": sorted(not_installed),
        "security_notes": [
            "OPENBRIDGE_ENABLE_LLM_VALIDATION=false keeps SQL validation heuristic-only and prevents SQL egress to LLM providers.",
            "When OPENBRIDGE_ENABLE_LLM_VALIDATION=true, SQL text may be sent to the configured OpenAI-compatible endpoint for validation.",
        ],
        "tools": tools,
    }


def get_capabilities(registered_tool_names: Set[str]) -> Dict[str, Any]:
    """Return current MCP tool availability and opt-in behavior."""
    return build_capabilities(registered_tool_names)
