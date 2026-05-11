"""In-memory lock provider for testing."""

from __future__ import annotations

import asyncio


class FakeLockProvider:
    """In-memory lock provider backed by ``asyncio.Lock`` per key.

    Locks are created lazily on the first ``acquire()`` call for a given
    key.  **Important:** this is a process-local, in-memory implementation
    only.  It does **not** replace optimistic concurrency checks in the
    domain layer (e.g. aggregate version checks).  Use a distributed lock
    provider (Redis, ZooKeeper, etc.) in multi-instance deployments.

    .. warning::

       Locks are **NOT reentrant**. Calling ``acquire()`` for the same
       key twice from the same task will **deadlock**.

    .. note::

       Lock entries accumulate over the process lifetime and are never
       evicted. For long-running processes or unbounded key spaces, use
       a distributed lock provider with TTL-based eviction.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    async def acquire(self, key: str) -> None:
        """Acquire the lock for *key*, creating one if necessary."""
        # Safe under asyncio cooperative multitasking: no await between
        # check and assignment. Do NOT insert an await between these lines.
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        await self._locks[key].acquire()

    async def release(self, key: str) -> None:
        """Release the lock for *key*.

        Raises ``KeyError`` if no lock exists for *key*.
        """
        if key not in self._locks:
            msg = f"No lock registered for key: {key}"
            raise KeyError(msg)
        self._locks[key].release()
