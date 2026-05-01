"""Code mode integration for Openbridge MCP.

When enabled, Code Mode replaces the exposed tool catalog with lightweight
meta-tools (for discovery + execute).

Beyond the basic transform, this module also defines
``_EnvelopeUnwrappingCodeMode`` — a thin subclass of FastMCP's ``CodeMode``
that converts v1 error envelopes raised as ``ToolError(json.dumps(envelope))``
into return values inside the sandboxed ``call_tool`` shim. Without it,
sandboxed user code has to do ``except: env = json.loads(str(e))`` to
recover from envelope-shaped errors, which conflates legitimate Python
exceptions with contract-conformant errors and breaks the documented
``if isinstance(err, dict) and err.get("error_kind"): ...`` recovery
pattern in ``CONTRACT.md``.

The unwrap layer applies *only* to the sandbox path. Direct MCP-transport
callers continue to receive ``ToolError`` raises — that's a wire-protocol
constraint (``ToolResult`` has no ``isError`` field), not fixable from our
side. See ``CONTRACT.md`` §"Receiving envelopes" for the asymmetry.
"""

from __future__ import annotations

import json
import os
from typing import Annotated, Any, Awaitable, Callable, Optional

from src.utils.envelope import make_error

_ENVELOPE_TOOL_ERROR_PREFIX = "ToolError: "


# ---------------------------------------------------------------------------
# Module-level imports of the FastMCP code-mode extras.
#
# Pydantic resolves the ``Annotated[str, Field(...)]`` annotation on the
# ``execute`` function (defined inside ``_make_execute_tool``) against the
# *module globals* of the file that declared it — see
# ``pydantic._internal._typing_extra.eval_type_backport``. If we imported
# ``Annotated``, ``Field``, ``Context``, etc. lazily inside a builder
# function, Pydantic would raise ``NameError: name 'Annotated' is not
# defined`` at FastMCP server startup when it introspects the tool's
# signature.
#
# Imports are guarded so the module still imports cleanly when the
# ``code-mode`` extras are absent (e.g. minimal CI environments). The
# subclass and ``create_code_mode_transform`` raise ImportError in that
# case — same UX as before this change.
# ---------------------------------------------------------------------------

try:
    # Only the subclass-construction symbols live at module scope so
    # Pydantic can resolve the ``execute`` function's annotations. The
    # discovery-tool / sandbox-provider symbols are re-imported lazily
    # inside ``create_code_mode_transform`` to keep the meta-path-blocker
    # import-error test working.
    from fastmcp.exceptions import ToolError
    from fastmcp.experimental.transforms.code_mode import (
        CodeMode,
        _unwrap_tool_result,
    )
    from fastmcp.server.context import Context
    from fastmcp.tools.base import Tool
    from pydantic import Field

    _CODE_MODE_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when extras absent
    _CODE_MODE_AVAILABLE = False
    CodeMode = object  # type: ignore[assignment,misc]
    ToolError = Exception  # type: ignore[assignment,misc]


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def is_code_mode_enabled() -> bool:
    """Return True when code mode should be active."""
    return _env_bool("CODE_MODE", True)


def _maybe_envelope_from_tool_error(exc: Exception) -> Optional[dict]:
    """Return the v1 envelope dict if ``exc.message`` carries one, else None.

    Tolerant parser:

    - Strips an optional ``"ToolError: "`` prefix in case a future FastMCP
      release changes how it formats ``str(exc)``. Today it doesn't add a
      prefix; the strip is defensive belt-and-suspenders.
    - Parses the (possibly de-prefixed) message as JSON and returns the
      payload only if it is a dict with ``_envelope_version == 1``.
    - Anything else — non-JSON message, JSON that isn't a dict, dict
      without the version discriminant, dict with a different version —
      returns ``None`` so the caller re-raises and the contract failure
      surfaces as a real exception, not a silent swallow.
    """
    msg = str(exc)
    if msg.startswith(_ENVELOPE_TOOL_ERROR_PREFIX):
        msg = msg[len(_ENVELOPE_TOOL_ERROR_PREFIX):]
    try:
        envelope = json.loads(msg)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(envelope, dict):
        return None
    if envelope.get("_envelope_version") != 1:
        return None
    return envelope


def _wrap_call_tool_with_envelope_unwrap(
    inner: Callable[[str, dict[str, Any]], Awaitable[Any]],
) -> Callable[[str, dict[str, Any]], Awaitable[Any]]:
    """Wrap an async ``call_tool`` so v1 envelope ``ToolError`` raises return.

    Only ``ToolError`` whose message decodes to a v1 envelope is converted.
    Everything else (non-envelope ``ToolError``, ``RuntimeError``,
    ``asyncio.CancelledError``, etc.) propagates unchanged so the sandbox
    can still see real Python exceptions and asyncio cancellation works.
    """

    async def wrapped(tool_name: str, params: dict[str, Any]) -> Any:
        try:
            return await inner(tool_name, params)
        except ToolError as exc:
            envelope = _maybe_envelope_from_tool_error(exc)
            if envelope is not None:
                return envelope
            raise

    return wrapped


# ---------------------------------------------------------------------------
# CodeMode subclass that applies the wrap at the sandbox boundary.
# ---------------------------------------------------------------------------


if _CODE_MODE_AVAILABLE:

    class _EnvelopeUnwrappingCodeMode(CodeMode):  # type: ignore[misc,valid-type]
        """CodeMode subclass that unwraps v1 envelopes from sandbox raises.

        Why: the upstream ``call_tool`` shim
        (``fastmcp.experimental.transforms.code_mode.CodeMode._make_execute_tool``)
        propagates ``ToolError`` unchanged. ``ErrorEnvelopeMiddleware`` packs
        v1 envelopes into ``ToolError.message`` as JSON; sandboxed user
        code then has to ``json.loads(str(exc))`` to recover. This subclass
        moves that decode to the boundary so sandbox callers can write the
        documented ``if isinstance(err, dict) and err.get("error_kind"): ...``
        pattern without a try/except.

        Implementation: override ``_make_execute_tool`` and rebuild the
        upstream body, wrapping the inner ``call_tool`` at the
        ``external_functions`` boundary (per-call, not captured at init).
        See ``_wrap_call_tool_with_envelope_unwrap`` for the wrap logic.

        Upstream coupling note: this method copies the body of
        ``CodeMode._make_execute_tool`` from FastMCP 3.2.x. If a future
        FastMCP release renames or restructures the upstream method, the
        ``test_envelope_unwrapping_codemode_overrides_make_execute_tool``
        test will fail loudly — early signal of upstream drift.
        """

        def _make_execute_tool(self) -> "Tool":  # type: ignore[override]
            transform = self

            async def execute(
                code: Annotated[
                    str,
                    Field(
                        description=(
                            "Python async code to execute tool calls via call_tool(name, arguments)"
                        )
                    ),
                ],
                ctx: Context = None,  # type: ignore[assignment]
            ) -> Any:
                """Execute tool calls using Python code."""

                async def call_tool(tool_name: str, params: dict[str, Any]) -> Any:
                    backend_tools = await transform.get_tool_catalog(ctx)
                    tool = transform._find_tool(tool_name, backend_tools)
                    if tool is None:
                        return make_error(
                            tool=tool_name,
                            error_kind="tool_not_found",
                            summary=f"Tool '{tool_name}' is not registered on this deployment",
                            error_code="TOOL_NOT_FOUND",
                            retryable=False,
                            details=[{
                                "path": "tool_name",
                                "issue": "Requested tool name is not present in the active sandbox catalog",
                                "received_type": type(tool_name).__name__,
                            }],
                            hints=[
                                "Call get_capabilities to inspect installed tools.",
                                "Check capabilities.not_installed for deployment-gated tools.",
                            ],
                        )
                    result = await ctx.fastmcp.call_tool(tool.name, params)
                    return _unwrap_tool_result(result)

                wrapped_call_tool = _wrap_call_tool_with_envelope_unwrap(call_tool)
                return await transform.sandbox_provider.run(
                    code,
                    external_functions={"call_tool": wrapped_call_tool},
                )

            return Tool.from_function(
                fn=execute,
                name=self.execute_tool_name,
                description=self._build_execute_description(),
            )

else:  # pragma: no cover - exercised only when extras absent
    _EnvelopeUnwrappingCodeMode = None  # type: ignore[assignment]


def create_code_mode_transform():
    """Create a configured CodeMode transform.

    Returns an ``_EnvelopeUnwrappingCodeMode`` (a ``CodeMode`` subclass)
    so v1 error envelopes raised by ``ErrorEnvelopeMiddleware`` arrive
    in sandboxed code as return values, not exceptions.

    Raises:
        ImportError: if FastMCP code-mode extras are unavailable.
    """
    # Re-import at call time so a runtime-injected meta-path blocker (used
    # by ``test_create_code_mode_transform_raises_import_error_without_extras``)
    # still surfaces a clean ImportError. The module-level imports above
    # are needed for Pydantic to resolve the ``execute`` function's type
    # hints; this lazy import is the operator-facing failure path.
    try:
        from fastmcp.experimental.transforms.code_mode import (  # noqa: F401
            CodeMode as _RuntimeCodeMode,  # noqa: F401
            GetSchemas as _RuntimeGetSchemas,
            GetTags as _RuntimeGetTags,
            MontySandboxProvider as _RuntimeMontySandboxProvider,
            Search as _RuntimeSearch,
        )
    except ImportError as exc:
        raise ImportError(
            "Code mode requires fastmcp experimental transforms. "
            "Install with: pip install 'fastmcp[code-mode]>=3.1.0'"
        ) from exc

    if _EnvelopeUnwrappingCodeMode is None:  # pragma: no cover - defensive
        raise ImportError("_EnvelopeUnwrappingCodeMode could not be constructed")

    include_tags = _env_bool("CODE_MODE_INCLUDE_TAGS", True)
    max_duration_secs = float(os.getenv("CODE_MODE_MAX_DURATION_SECS", "30"))
    max_memory = int(os.getenv("CODE_MODE_MAX_MEMORY", "50000000"))

    discovery_tools = []
    if include_tags:
        discovery_tools.append(_RuntimeGetTags())
    discovery_tools.extend([_RuntimeSearch(), _RuntimeGetSchemas()])

    sandbox = _RuntimeMontySandboxProvider(
        limits={
            "max_duration_secs": max_duration_secs,
            "max_memory": max_memory,
        }
    )

    return _EnvelopeUnwrappingCodeMode(
        sandbox_provider=sandbox,
        discovery_tools=discovery_tools,
    )
