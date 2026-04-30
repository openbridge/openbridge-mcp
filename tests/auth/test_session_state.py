"""Tests for ``src.auth.session_state``.

The session state module exposes a ``ContextVar`` used by the auth
middleware to propagate the resolved JWT across async task boundaries,
including into Code Mode's sandboxed tool invocations (which receive a
fresh FastMCP ``Context``). These tests lock the contract: defaults
return ``None``, set/get round-trip within a task, and — most
importantly — concurrent tasks must not leak tokens across each other.

If someone downgrades the ContextVar to a plain module-level variable,
``test_concurrent_tasks_do_not_leak`` must fail loudly.
"""

import asyncio

from src.auth import session_state


def test_default_is_none():
    """Before any set call in this task, the token is None."""
    # Use a fresh task to guarantee no prior set state leaks in from
    # other tests running in the same event loop.
    async def _run():
        return session_state.get_request_jwt()

    assert asyncio.run(_run()) is None


def test_set_get_roundtrip():
    """Within the same async task, set → get returns the value."""
    async def _run():
        session_state.set_request_jwt("token-abc")
        return session_state.get_request_jwt()

    assert asyncio.run(_run()) == "token-abc"


def test_set_none_clears_value():
    """Setting None must surface as None (not the previous token)."""
    async def _run():
        session_state.set_request_jwt("token-before")
        session_state.set_request_jwt(None)
        return session_state.get_request_jwt()

    assert asyncio.run(_run()) is None


def test_concurrent_tasks_do_not_leak():
    """REGRESSION: two concurrent tasks must see only their own JWT.

    Each task sets a distinct token, yields control (giving the other
    task a chance to run and possibly clobber shared state), then reads
    its token back. With a proper ``ContextVar``, both reads return
    the per-task value. If someone downgrades the storage to a
    module-level ``Optional[str]``, the last writer wins and this test
    fails.
    """

    async def _task(label: str, delay: float) -> str:
        session_state.set_request_jwt(label)
        # Yield to the event loop a few times so the tasks interleave.
        await asyncio.sleep(delay)
        await asyncio.sleep(0)
        return session_state.get_request_jwt()

    async def _run():
        return await asyncio.gather(
            _task("jwt-A", 0.01),
            _task("jwt-B", 0.005),
            _task("jwt-C", 0.015),
        )

    results = asyncio.run(_run())
    assert results == ["jwt-A", "jwt-B", "jwt-C"], (
        f"ContextVar leaked across concurrent tasks: got {results}. "
        "This likely means session_state._jwt_var was downgraded to "
        "module-level storage."
    )
