import os
from typing import Any, Dict, List, Optional, Set

from src.server.code_mode import is_code_mode_enabled
from .tool_manifest import TOOL_MANIFEST


def _env_present(name: str) -> bool:
    value = os.getenv(name)
    return bool(value and value.strip())


def build_capabilities(registered_tool_names: Optional[Set[str]] = None) -> Dict[str, Any]:
    """Build capability metadata for the current MCP runtime."""
    has_sampling_key = _env_present("FASTMCP_SAMPLING_API_KEY") or _env_present("OPENAI_API_KEY")
    llm_opt_in = os.getenv("OPENBRIDGE_ENABLE_LLM_VALIDATION", "false").lower() == "true"

    tools: List[Dict[str, Any]] = []
    for tool_name, meta in TOOL_MANIFEST.items():
        if registered_tool_names is not None:
            enabled = tool_name in registered_tool_names
        elif tool_name in {"validate_query", "execute_query"}:
            enabled = has_sampling_key
        else:
            enabled = True

        tool: Dict[str, Any] = {
            "name": tool_name,
            "category": meta["category"],
            "enabled": enabled,
        }

        if tool_name in {"validate_query", "execute_query"}:
            tool["requires_env"] = ["FASTMCP_SAMPLING_API_KEY or OPENAI_API_KEY"]
            tool["llm_opt_in_required"] = True
            tool["llm_validation_enabled"] = bool(enabled and llm_opt_in)
            if not enabled:
                tool["disabled_reason"] = "missing_sampling_key" if not has_sampling_key else "not_registered"

        tools.append(tool)

    return {
        "summary": {
            "total_tools_declared": len(tools),
            "enabled_tools": len([tool for tool in tools if tool["enabled"]]),
            "disabled_tools": len([tool for tool in tools if not tool["enabled"]]),
        },
        "runtime": {
            "sampling_key_present": has_sampling_key,
            "llm_validation_enabled": llm_opt_in,
            "code_mode_enabled": is_code_mode_enabled(),
        },
        "security_notes": [
            "OPENBRIDGE_ENABLE_LLM_VALIDATION=false keeps SQL validation heuristic-only and prevents SQL egress to LLM providers.",
            "When OPENBRIDGE_ENABLE_LLM_VALIDATION=true, SQL text may be sent to the configured OpenAI-compatible endpoint for validation.",
        ],
        "tools": tools,
    }


def get_capabilities() -> Dict[str, Any]:
    """Return current MCP tool availability and opt-in behavior."""
    return build_capabilities()
