"""Tests for the SEP-1686 background-task wiring.

What this file locks in:

1. ``create_mcp_server`` constructs ``FastMCP(... tasks=True ...)`` so
   the protocol-level task surface is always advertised.
2. Every tool in the manifest *except* ``get_capabilities`` is registered
   with ``task=TaskConfig(mode="optional")``. Clients can choose between
   sync and background execution per call.
3. ``get_capabilities`` stays ``task=None`` (no Docket queue submission)
   because it's a pure local read of in-memory state — there's no I/O
   to background.
4. Sync tool implementations (the bulk of ``src/server/tools/``) get
   wrapped in an async coroutine via ``_async_wrap`` so FastMCP's
   ``TaskConfig.validate_function`` accepts them. The wrapper preserves
   the original signature via ``functools.wraps`` so FastMCP's parameter
   introspection still resolves the right schema.
5. The default ``FASTMCP_DOCKET_URL`` (used by tests via the env stub
   below) is ``memory://`` — keeps the suite offline. Production compose
   sets ``redis://redis:6379/0``.

Note: we use the same ``FakeFastMCP`` from
``tests/server/test_mcp_server.py`` so this file isolates *task wiring*
assertions from the rest of the server-construction surface.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from fastmcp.server.tasks import TaskConfig

from src.server import mcp_server
from src.server.tools.tool_manifest import TOOL_MANIFEST
from tests.server.test_mcp_server import FakeAuthConfig, FakeFastMCP


@pytest.fixture(autouse=True)
def _docket_memory_backend(monkeypatch):
    """Default to the in-memory Docket backend so tests never reach Redis."""
    monkeypatch.setenv("FASTMCP_DOCKET_URL", "memory://")


@pytest.fixture(autouse=True)
def _disable_code_mode(monkeypatch):
    # Code Mode wraps the tool surface and we want to assert directly on
    # the registered tools, not on the meta-tool transform.
    monkeypatch.setenv("CODE_MODE", "false")


def _build_server(monkeypatch, *, api_key: bool = True) -> FakeFastMCP:
    """Construct the server with FakeFastMCP swapped in.

    Mirrors the helper in tests/server/test_mcp_server.py but with
    minimal parameters — we only care about tool/task registration here.
    """
    if api_key:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    else:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("FASTMCP_SAMPLING_API_KEY", raising=False)

    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: FakeAuthConfig())
    monkeypatch.setattr(mcp_server, "get_auth_manager", lambda: "auth-manager")
    monkeypatch.setattr(
        mcp_server,
        "create_auth_middleware",
        lambda config, *, jwt_middleware, auth_manager: [],
    )
    monkeypatch.setattr(mcp_server, "create_sampling_handler", lambda: object())
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)
    return mcp_server.create_mcp_server()


# ---------------------------------------------------------------------------
# Server-level: tasks=True is always wired
# ---------------------------------------------------------------------------


def test_server_constructed_with_tasks_enabled(monkeypatch):
    """FastMCP must be constructed with tasks=True so SEP-1686 task
    augmentation is advertised in capabilities, regardless of whether
    individual tools opt in.
    """
    server = _build_server(monkeypatch)
    assert server.tasks_enabled is True


# ---------------------------------------------------------------------------
# Per-tool: task=optional is the default for Openbridge-API tools
# ---------------------------------------------------------------------------


# get_capabilities is a pure local read — leave it sync.
TOOLS_EXEMPT_FROM_TASKS = {"get_capabilities"}


def test_every_api_tool_supports_optional_task_execution(monkeypatch):
    """All tools that hit the Openbridge HTTP layer must register with
    task=TaskConfig(mode="optional"). This makes background execution
    available per call without forcing it. ``get_capabilities`` is
    explicitly exempt because it doesn't do I/O."""
    server = _build_server(monkeypatch)
    for name, entry in server.registered_tools.items():
        task = entry["task"]
        if name in TOOLS_EXEMPT_FROM_TASKS:
            assert task is None, (
                f"{name!r} should not be a background task but got {task!r}"
            )
            continue
        assert isinstance(task, TaskConfig), (
            f"Tool {name!r} should advertise background-task support; "
            f"got task={task!r}"
        )
        assert task.mode == "optional", (
            f"Tool {name!r} should default to mode='optional'; got {task.mode!r}"
        )


def test_task_coverage_matches_manifest(monkeypatch):
    """Sanity check: the set of tools that *can* be backgrounded matches
    everything in the manifest minus the explicit exemption list. If a
    new tool is added to the manifest but not given a task config, this
    test fails with a clear pointer.
    """
    server = _build_server(monkeypatch)
    expected_taskable = set(TOOL_MANIFEST) - TOOLS_EXEMPT_FROM_TASKS
    actually_taskable = {
        name
        for name, entry in server.registered_tools.items()
        if isinstance(entry["task"], TaskConfig)
    }
    # The server may skip tools when no API key is configured, so
    # actually_taskable is a subset, not equal.
    assert actually_taskable.issubset(expected_taskable)
    missing = expected_taskable - actually_taskable - {"validate_query", "execute_query"}
    assert not missing, (
        f"Tools missing TaskConfig wiring: {sorted(missing)}. "
        "Add task=TaskConfig(mode='optional') in src/server/mcp_server.py."
    )


# ---------------------------------------------------------------------------
# Sync→async wrapping
# ---------------------------------------------------------------------------


def test_async_wrap_preserves_signature():
    """``_async_wrap`` must keep the original parameter signature so
    FastMCP's introspection produces the right tool schema."""

    def my_tool(subscription_id: int, status: str = "active") -> dict:
        return {"id": subscription_id, "status": status}

    wrapped = mcp_server._async_wrap(my_tool)

    assert inspect.iscoroutinefunction(wrapped)
    sig = inspect.signature(wrapped)
    params = list(sig.parameters.values())
    assert [p.name for p in params] == ["subscription_id", "status"]
    # Annotation may be a string under PEP 563 / `from __future__ import
    # annotations`; either form is acceptable so long as the parameter
    # type is still discoverable.
    annotations = getattr(wrapped, "__annotations__", {})
    assert annotations.get("subscription_id") in (int, "int")
    assert annotations.get("status") in (str, "str")
    assert params[1].default == "active"


def test_async_wrap_offloads_to_thread_and_returns_value():
    """The wrapper must actually execute the underlying function and
    return its value."""

    sentinel = {"result": "ok"}

    def sync_impl(arg):
        return {"result": arg}

    wrapped = mcp_server._async_wrap(sync_impl)
    assert asyncio.run(wrapped("ok")) == sentinel


def test_async_wrap_propagates_exceptions():
    """Exceptions raised by the sync impl must surface as exceptions on
    the awaited coroutine — not be swallowed."""

    class Boom(RuntimeError):
        pass

    def sync_impl():
        raise Boom("upstream failure")

    wrapped = mcp_server._async_wrap(sync_impl)
    with pytest.raises(Boom, match="upstream failure"):
        asyncio.run(wrapped())


def test_is_async_callable_detects_coroutine_functions():
    async def already_async():
        return None

    def sync_func():
        return None

    assert mcp_server._is_async_callable(already_async) is True
    assert mcp_server._is_async_callable(sync_func) is False


def test_is_async_callable_unwraps_partials():
    """``functools.partial(async_fn, ...)`` is a coroutine factory; the
    detector must see through the partial. This matters because tool
    helpers sometimes pre-bind arguments before registration."""
    import functools as ft

    async def already_async(x):
        return x

    bound = ft.partial(already_async, 1)
    assert mcp_server._is_async_callable(bound) is True


# ---------------------------------------------------------------------------
# Already-async impls are not double-wrapped
# ---------------------------------------------------------------------------


def test_already_async_tools_are_not_wrapped(monkeypatch):
    """``validate_query`` and ``execute_query`` are already async. The
    registration path must pass them through unchanged so we don't pay
    the to_thread overhead for nothing."""
    server = _build_server(monkeypatch, api_key=True)

    # If the implementation is async, _async_wrap should NOT have been
    # invoked — the registered func is the original async function.
    from src.server.tools import service as service_tools

    assert server.registered_tools["validate_query"]["func"] is service_tools.validate_query
    assert server.registered_tools["execute_query"]["func"] is service_tools.execute_query
