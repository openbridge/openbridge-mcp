"""Tests for the sandbox `call_tool` envelope-unwrap layer (v0.2.3).

The middleware (`ErrorEnvelopeMiddleware`) packs v1 error envelopes into
``ToolError(json.dumps(envelope))`` and raises. That is correct on the
direct MCP transport path. Inside Code Mode, however, sandboxed user code
cannot easily distinguish "envelope shipped as exception" from a real
Python exception — it has to do ``except: env = json.loads(str(e))``,
which conflates legitimate errors with envelope-shaped errors.

`_EnvelopeUnwrappingCodeMode` and its two helpers
(`_maybe_envelope_from_tool_error`, `_wrap_call_tool_with_envelope_unwrap`)
fix this asymmetry: when the inner ``call_tool`` raises a `ToolError`
whose message is a v1 envelope, the wrapper returns the envelope dict
instead of re-raising. Everything else propagates unchanged.

Test layout:

- ``TestMaybeEnvelopeFromToolError`` — pure parser tolerance.
- ``TestWrappedCallTool`` — wrapper behavior given a synthetic ``inner``.
- ``TestSubclassWiring`` — `_EnvelopeUnwrappingCodeMode` is the class that
  ``create_code_mode_transform`` returns and is a subclass of ``CodeMode``.

The full integration assertion (real sandbox executing real Python code
that calls ``call_tool``) is deferred to the live skill re-test against
the deployed v0.2.3 server — exercising the Monty subprocess sandbox in
unit tests is heavy and adds little above the wrapper-level coverage
here.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastmcp.exceptions import ToolError
from fastmcp.tools.base import Tool


# ---------------------------------------------------------------------------
# Pure-parser tolerance
# ---------------------------------------------------------------------------


class TestMaybeEnvelopeFromToolError:
    """`_maybe_envelope_from_tool_error` returns the dict when the message
    carries a v1 envelope, otherwise None — never raises."""

    def test_recognizes_v1_envelope(self):
        from src.server.code_mode import _maybe_envelope_from_tool_error

        envelope = {
            "_envelope_version": 1,
            "error_kind": "mcp_input_validation",
            "tool": "get_jobs",
            "summary": "bad input",
            "details": [],
            "hints": [],
            "examples": [],
            "error_code": "INPUT_VALIDATION_FAILED",
            "retryable": False,
        }
        exc = ToolError(json.dumps(envelope))

        assert _maybe_envelope_from_tool_error(exc) == envelope

    def test_recognizes_envelope_with_tool_error_prefix(self):
        """Defensive: tolerate FastMCP changing how it formats ``str(exc)`` by
        prepending ``'ToolError: '``. Today it doesn't, but a future minor
        release might — the wrapper must not regress silently if so."""
        from src.server.code_mode import _maybe_envelope_from_tool_error

        envelope = {
            "_envelope_version": 1,
            "error_kind": "internal_error",
            "tool": "x",
            "summary": "y",
            "details": [],
            "hints": [],
            "examples": [],
            "error_code": "INTERNAL_ERROR",
            "retryable": False,
        }
        exc = ToolError("ToolError: " + json.dumps(envelope))

        assert _maybe_envelope_from_tool_error(exc) == envelope

    def test_returns_none_for_non_json_message(self):
        from src.server.code_mode import _maybe_envelope_from_tool_error

        exc = ToolError("plain string, not JSON at all")
        assert _maybe_envelope_from_tool_error(exc) is None

    def test_returns_none_for_json_that_is_not_a_dict(self):
        from src.server.code_mode import _maybe_envelope_from_tool_error

        exc = ToolError(json.dumps([1, 2, 3]))
        assert _maybe_envelope_from_tool_error(exc) is None

    def test_returns_none_for_dict_without_envelope_version(self):
        from src.server.code_mode import _maybe_envelope_from_tool_error

        exc = ToolError(json.dumps({"foo": "bar"}))
        assert _maybe_envelope_from_tool_error(exc) is None

    def test_returns_none_for_wrong_envelope_version(self):
        from src.server.code_mode import _maybe_envelope_from_tool_error

        exc = ToolError(json.dumps({"_envelope_version": 2, "error_kind": "x"}))
        assert _maybe_envelope_from_tool_error(exc) is None


# ---------------------------------------------------------------------------
# Wrapper behavior
# ---------------------------------------------------------------------------


def _make_envelope() -> dict:
    return {
        "_envelope_version": 1,
        "error_kind": "mcp_input_validation",
        "tool": "get_jobs",
        "summary": "bogus_kwarg is not a recognized parameter",
        "details": [{"path": "bogus_kwarg", "issue": "extra_forbidden", "received_type": "str"}],
        "hints": [],
        "examples": [],
        "error_code": "INPUT_VALIDATION_FAILED",
        "retryable": False,
    }


class TestWrappedCallTool:
    """`_wrap_call_tool_with_envelope_unwrap` converts v1-envelope ToolErrors
    to return values; everything else propagates unchanged."""

    @pytest.mark.asyncio
    async def test_envelope_tool_error_is_returned_as_dict(self):
        """Primary repro: middleware raises ``ToolError(json.dumps(envelope))``
        — sandbox callers should receive the envelope as a return value."""
        from src.server.code_mode import _wrap_call_tool_with_envelope_unwrap

        envelope = _make_envelope()

        async def inner(tool_name, params):
            raise ToolError(json.dumps(envelope))

        wrapped = _wrap_call_tool_with_envelope_unwrap(inner)
        result = await wrapped("get_jobs", {"bogus_kwarg": "x"})

        assert result == envelope
        assert isinstance(result, dict)
        assert result.get("_envelope_version") == 1
        assert result.get("error_kind") == "mcp_input_validation"

    @pytest.mark.asyncio
    async def test_non_envelope_tool_error_is_re_raised(self):
        """A ToolError carrying a plain (non-JSON) message must propagate
        unchanged — never silently swallowed."""
        from src.server.code_mode import _wrap_call_tool_with_envelope_unwrap

        async def inner(tool_name, params):
            raise ToolError("upstream said no")

        wrapped = _wrap_call_tool_with_envelope_unwrap(inner)

        with pytest.raises(ToolError) as exc_info:
            await wrapped("x", {})

        assert "upstream said no" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_tool_error_with_wrong_envelope_version_is_re_raised(self):
        """JSON-shaped ToolError that lacks ``_envelope_version == 1`` is
        also re-raised — we only unwrap the *current* contract version."""
        from src.server.code_mode import _wrap_call_tool_with_envelope_unwrap

        async def inner(tool_name, params):
            raise ToolError(json.dumps({"_envelope_version": 2, "shape": "future"}))

        wrapped = _wrap_call_tool_with_envelope_unwrap(inner)

        with pytest.raises(ToolError):
            await wrapped("x", {})

    @pytest.mark.asyncio
    async def test_non_tool_error_exception_is_re_raised(self):
        """RuntimeError, KeyError, etc. must NOT be caught — only ToolError
        is in scope. The sandbox needs to see real Python errors."""
        from src.server.code_mode import _wrap_call_tool_with_envelope_unwrap

        async def inner(tool_name, params):
            raise RuntimeError("boom")

        wrapped = _wrap_call_tool_with_envelope_unwrap(inner)

        with pytest.raises(RuntimeError) as exc_info:
            await wrapped("x", {})

        assert str(exc_info.value) == "boom"

    @pytest.mark.asyncio
    async def test_cancelled_error_is_re_raised(self):
        """CancelledError must always propagate so asyncio cancellation
        semantics aren't broken by the wrapper."""
        from src.server.code_mode import _wrap_call_tool_with_envelope_unwrap

        async def inner(tool_name, params):
            raise asyncio.CancelledError()

        wrapped = _wrap_call_tool_with_envelope_unwrap(inner)

        with pytest.raises(asyncio.CancelledError):
            await wrapped("x", {})

    @pytest.mark.asyncio
    async def test_successful_call_passes_through_unchanged(self):
        """Happy path: when the inner returns normally, the wrapper hands
        the result back verbatim — envelope-unwrap is gated on the
        ``_envelope_version == 1`` discriminant, nothing else is touched."""
        from src.server.code_mode import _wrap_call_tool_with_envelope_unwrap

        sentinel = {"data": [{"id": 1, "name": "row"}], "count": 1}

        async def inner(tool_name, params):
            return sentinel

        wrapped = _wrap_call_tool_with_envelope_unwrap(inner)

        assert await wrapped("get_jobs", {"subscription_id": 1}) is sentinel


# ---------------------------------------------------------------------------
# Subclass wiring
# ---------------------------------------------------------------------------


class TestSubclassWiring:
    """``create_code_mode_transform`` must return a CodeMode that wraps the
    inner ``call_tool`` shim with envelope-unwrap semantics. We can't easily
    fire real code through the sandbox subprocess in a unit test, so we
    smoke-check the class hierarchy and that the override hook exists."""

    def test_create_code_mode_transform_returns_envelope_unwrapping_subclass(self):
        from fastmcp.experimental.transforms.code_mode import CodeMode

        from src.server.code_mode import (
            _EnvelopeUnwrappingCodeMode,
            create_code_mode_transform,
        )

        transform = create_code_mode_transform()
        assert isinstance(transform, _EnvelopeUnwrappingCodeMode)
        assert isinstance(transform, CodeMode)

    def test_envelope_unwrapping_codemode_overrides_make_execute_tool(self):
        """Strategy A's whole premise: we override ``_make_execute_tool``.
        If a future FastMCP release renames the method, this test fails
        loudly — early signal of upstream coupling drift."""
        from fastmcp.experimental.transforms.code_mode import CodeMode

        from src.server.code_mode import _EnvelopeUnwrappingCodeMode

        assert (
            _EnvelopeUnwrappingCodeMode._make_execute_tool
            is not CodeMode._make_execute_tool
        )


# ---------------------------------------------------------------------------
# Thin integration on the execute-tool boundary
# ---------------------------------------------------------------------------


class _StubSandboxProvider:
    def __init__(self, target_name: str, target_params: dict[str, Any]):
        self._target_name = target_name
        self._target_params = target_params

    async def run(self, code: str, external_functions: dict[str, Any]) -> Any:
        # Ignore `code`; directly exercise the injected call_tool shim.
        return await external_functions["call_tool"](
            self._target_name,
            self._target_params,
        )


class _StubFastMCP:
    def __init__(self):
        self._tool = Tool.from_function(
            fn=lambda subscription_id: {"ok": True, "subscription_id": subscription_id},
            name="get_jobs",
            description="stub",
        )

    async def list_tools(self, run_middleware: bool = True):
        return [self._tool]

    async def call_tool(self, name: str, params: dict[str, Any]):
        # This call path should not be reached in unknown-tool tests.
        return {"name": name, "params": params}


class _StubCtx:
    def __init__(self):
        self.fastmcp = _StubFastMCP()


class TestExecuteBoundary:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_tool_not_found_envelope(self):
        """Unknown tool lookup in sandbox must return v1 envelope value."""
        from src.server.code_mode import _EnvelopeUnwrappingCodeMode

        transform = _EnvelopeUnwrappingCodeMode(
            sandbox_provider=_StubSandboxProvider("not_a_real_tool", {}),
            discovery_tools=[],
        )
        execute_tool = transform._make_execute_tool()

        result = await execute_tool.fn(code="ignored", ctx=_StubCtx())
        assert isinstance(result, dict)
        assert result.get("_envelope_version") == 1
        assert result.get("error_kind") == "tool_not_found"
        assert result.get("tool") == "not_a_real_tool"
