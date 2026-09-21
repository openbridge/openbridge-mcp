"""Bounded async coordination for synchronous token exchanges."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from src.utils.runtime_security import positive_int_env


class TokenExchangeCoordinator:
    """Offload exchanges to a dedicated pool and deduplicate them by key."""

    def __init__(self, max_workers: int | None = None) -> None:
        workers = (
            max_workers
            if max_workers is not None
            else positive_int_env(
                "OPENBRIDGE_AUTH_EXCHANGE_CONCURRENCY",
                default=8,
            )
        )
        if workers < 1:
            raise ValueError("max_workers must be positive")
        self._executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="openbridge-auth",
        )
        self._semaphore = asyncio.Semaphore(workers)
        self._in_flight: dict[str, asyncio.Task[str]] = {}
        self._in_flight_lock = asyncio.Lock()

    async def resolve(self, key: str, operation: Callable[[], str]) -> str:
        """Return one shared result for concurrent operations with the same key."""
        async with self._in_flight_lock:
            task = self._in_flight.get(key)
            if task is None:
                task = asyncio.create_task(self._run(operation))
                self._in_flight[key] = task
                task.add_done_callback(
                    lambda completed, token_key=key: asyncio.create_task(
                        self._discard(token_key, completed)
                    )
                )
        return await asyncio.shield(task)

    async def _run(self, operation: Callable[[], str]) -> str:
        async with self._semaphore:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self._executor, operation)

    async def _discard(self, key: str, completed: asyncio.Task[str]) -> None:
        async with self._in_flight_lock:
            if self._in_flight.get(key) is completed:
                self._in_flight.pop(key, None)
