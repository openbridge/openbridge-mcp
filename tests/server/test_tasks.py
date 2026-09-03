"""Tests for the SEP-2663 background-task wiring.

What this file locks in:

1. ``create_mcp_server`` installs FastMCP 4's ``TasksExtension`` so the
   protocol-level task surface is always advertised.
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
import hashlib
import inspect
from pathlib import Path

import pytest
import jwt as pyjwt
from fastmcp import Client, Context, FastMCP
from fastmcp.exceptions import McpError
from fastmcp.utilities.tasks import TaskConfig
from fastmcp_tasks import TasksExtension
from fastmcp_tasks.client import call_tool_task
from fastmcp_tasks.context import get_task_context

from src.auth.authentication import OpenbridgeAuthMiddleware
from src.server import mcp_server
from src.server.tools.base import get_auth_headers
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
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)
    return mcp_server.create_mcp_server()


# ---------------------------------------------------------------------------
# Server-level: TasksExtension is always wired
# ---------------------------------------------------------------------------


def test_server_constructed_with_tasks_extension(monkeypatch):
    """FastMCP must install TasksExtension for SEP-2663 support."""
    server = _build_server(monkeypatch)
    assert len(server.extensions) == 1
    assert isinstance(server.extensions[0], mcp_server.TasksExtension)


@pytest.mark.asyncio
async def test_background_task_restores_openbridge_auth_and_tenant_scope(monkeypatch):
    """A real task worker must receive the submitting tenant's resolved JWT."""
    monkeypatch.setenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", "true")
    jwt_token = pyjwt.encode({"sub": "tenant-a"}, "a" * 32, algorithm="HS256")

    class StaticAuth:
        def get_jwt(self):
            return jwt_token

    server = FastMCP("task-auth-test")
    server.add_extension(TasksExtension(url="memory://"))
    server.add_middleware(OpenbridgeAuthMiddleware(StaticAuth()))

    @server.tool(task=TaskConfig(mode="optional"))
    async def observe_auth(ctx: Context) -> dict[str, str | None]:
        task = get_task_context()
        return {
            "authorization": get_auth_headers(ctx).get("Authorization"),
            "scope": task.task_scope if task else None,
        }

    async with Client(server) as client:
        result = await client.call_tool("observe_auth", raise_on_error=False)

    expected_scope = (
        "openbridge-mcp|credential:"
        f"{hashlib.sha256(jwt_token.encode()).hexdigest()}"
    )
    assert result.is_error is False, result.content
    assert result.data == {
        "authorization": f"Bearer {jwt_token}",
        "scope": expected_scope,
    }


@pytest.mark.asyncio
async def test_background_tasks_are_isolated_by_tenant_scope(monkeypatch):
    """A second tenant must not read a task submitted by the first tenant."""
    monkeypatch.setenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", "true")
    tenant_a = pyjwt.encode({"sub": "tenant-a"}, "a" * 32, algorithm="HS256")
    tenant_b = pyjwt.encode({"sub": "tenant-b"}, "b" * 32, algorithm="HS256")
    current_token = {"value": tenant_a}

    class DynamicAuth:
        def get_jwt(self):
            return current_token["value"]

    server = FastMCP("task-isolation-test")
    server.add_extension(TasksExtension(url="memory://"))
    server.add_middleware(OpenbridgeAuthMiddleware(DynamicAuth()))

    @server.tool(task=TaskConfig(mode="optional"))
    async def tenant_task() -> str:
        return "complete"

    async with Client(server) as client:
        task = await call_tool_task(client, "tenant_task")
        current_token["value"] = tenant_b
        with pytest.raises(McpError, match="not found"):
            await task.status()


def test_production_compose_requires_task_snapshot_encryption():
    """Persistent Redis task snapshots must have encryption configured."""
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    compose = compose_path.read_text()

    assert (
        "FASTMCP_TASKS_ENCRYPTION_KEY: "
        "${FASTMCP_TASKS_ENCRYPTION_KEY:?set FASTMCP_TASKS_ENCRYPTION_KEY in .env}"
    ) in compose


# ---------------------------------------------------------------------------
# Per-tool: task=optional is the default for Openbridge-API tools
# ---------------------------------------------------------------------------


# Pure local-read tools — registered with task=None so they don't go
# through the Docket queue. ``get_capabilities`` is the historical
# example; ``list_skills`` / ``read_skill`` were added in v0.3.4 and
# are similarly in-process (they call ctx.fastmcp.list_resources() /
# read_resource() directly, no HTTP).
TOOLS_EXEMPT_FROM_TASKS = {"get_capabilities", "list_skills", "read_skill"}


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
        assert isinstance(task, mcp_server.TaskConfig), (
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
        if isinstance(entry["task"], mcp_server.TaskConfig)
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
