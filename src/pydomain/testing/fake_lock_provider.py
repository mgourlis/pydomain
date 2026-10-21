"""In-memory lock provider for testing."""

from __future__ import annotations

import asyncio

from pydomain.cqrs.exceptions import LockReentryError
from pydomain.cqrs.locking import LockProvider

_REENTRY_MESSAGE = (
    "FakeLockProvider: the current task already holds this lock key; "
    "locks are not reentrant (ADR-076)"
)


class FakeLockProvider(LockProvider):
    """In-memory lock provider backed by ``asyncio.Lock`` per key.

    Locks are created lazily on the first ``acquire()`` call for a given
    key.  **Important:** this is a process-local, in-memory implementation
    only.  It does **not** replace optimistic concurrency checks in the
    domain layer (e.g. aggregate version checks).  Use a distributed lock
    provider (Redis, ZooKeeper, etc.) in multi-instance deployments.

    .. warning::

       Locks are **not reentrant** (ADR-076). When the task that holds a key
       calls ``acquire()`` for it again, the call raises
       :class:`~pydomain.cqrs.exceptions.LockReentryError` instead of
       deadlocking; the key stays held. "The same task" is the asyncio task
       (``asyncio.current_task()``), so a nested acquire from a task the
       holder spawned is a different task: it waits, and deadlocks if the
       holder waits on it.

    Any task may release a key, so ``AggregateLockingBehavior`` can release
    from its shielded release task.

    .. note::

       Lock entries accumulate over the process lifetime and are never
       evicted. For long-running processes or unbounded key spaces, use
       a distributed lock provider with TTL-based eviction.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._holders: dict[str, asyncio.Task[object] | None] = {}

    async def acquire(self, key: str) -> None:
        """Acquire the lock for *key*, creating one if necessary.

        Raises :class:`~pydomain.cqrs.exceptions.LockReentryError` if the
        current task already holds *key*. The message names no key.
        """
        # Safe under asyncio cooperative multitasking: no await between
        # check and assignment. Do NOT insert an await between these lines.
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        current = asyncio.current_task()
        if key in self._holders and self._holders[key] is current:
            raise LockReentryError(_REENTRY_MESSAGE)
        await self._locks[key].acquire()
        self._holders[key] = current

    async def release(self, key: str) -> None:
        """Release the lock for *key*.

        Raises ``KeyError`` if no lock exists for *key*.
        """
        if key not in self._locks:
            msg = f"No lock registered for key: {key}"
            raise KeyError(msg)
        self._holders.pop(key, None)
        self._locks[key].release()
