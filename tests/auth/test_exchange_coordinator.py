import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.auth.authentication import OpenbridgeAuthMiddleware
from src.auth.exchange_coordinator import TokenExchangeCoordinator
from src.auth.simple import _CachedToken, _InMemoryLRUTokenCache


@pytest.mark.asyncio
async def test_exchange_does_not_block_event_loop(monkeypatch):
    started = threading.Event()
    release_exchange = threading.Event()

    class BlockingAuth:
        def exchange_token(self, _token):
            started.set()
            if not release_exchange.wait(timeout=1):
                raise TimeoutError("test exchange was not released")
            return "tenant-jwt"

    context = SimpleNamespace(fastmcp_context=SimpleNamespace(set_state=lambda *_args: None))
    request = SimpleNamespace(headers={"authorization": "Bearer account123:secret123"})
    monkeypatch.setattr("src.auth.authentication.get_http_request", lambda: request)
    middleware = OpenbridgeAuthMiddleware(BlockingAuth())
    call_next = AsyncMock(return_value="ok")

    exchange_task = asyncio.create_task(middleware.on_request(context, call_next))
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    try:
        await asyncio.sleep(0.05)
        assert loop.time() - started_at < 0.25
    finally:
        release_exchange.set()
    try:
        await exchange_task
    except TimeoutError:
        pass


@pytest.mark.asyncio
async def test_coordinator_bounds_submitted_operations():
    coordinator = TokenExchangeCoordinator(max_workers=2)
    entered = [threading.Event() for _ in range(3)]
    releases = [threading.Event() for _ in range(3)]

    def operation(index):
        entered[index].set()
        if not releases[index].wait(timeout=1):
            raise TimeoutError(f"operation {index} was not released")
        return str(index)

    tasks = [
        asyncio.create_task(
            coordinator.resolve(f"token:{index}", lambda index=index: operation(index))
        )
        for index in range(3)
    ]
    try:
        assert await asyncio.to_thread(entered[0].wait, 0.5)
        assert await asyncio.to_thread(entered[1].wait, 0.5)
        assert not entered[2].is_set()
        releases[0].set()
        assert await asyncio.to_thread(entered[2].wait, 0.5)
    finally:
        for event in releases:
            event.set()
    assert await asyncio.gather(*tasks) == ["0", "1", "2"]


@pytest.mark.asyncio
async def test_coordinator_deduplicates_same_token():
    coordinator = TokenExchangeCoordinator(max_workers=2)
    calls = 0
    lock = threading.Lock()

    def operation():
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.05)
        return "shared-jwt"

    results = await asyncio.gather(
        *(coordinator.resolve("client:same", operation) for _ in range(10))
    )

    assert results == ["shared-jwt"] * 10
    assert calls == 1


def test_lru_cache_is_thread_safe_and_bounded():
    cache = _InMemoryLRUTokenCache(max_entries=8)

    def churn(index):
        key = f"token-{index % 16}"
        cache.set(key, _CachedToken(token=f"jwt-{index}", expires=time.time() + 3600))
        cache.get(key)
        tuple(cache)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(churn, range(500)))

    assert len(cache) <= 8
    assert all(isinstance(cache.get(key), _CachedToken) for key in tuple(cache))
