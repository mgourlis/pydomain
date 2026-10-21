"""In-memory lock provider for testing."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar

from pydomain.cqrs.exceptions import LockReentryError
from pydomain.cqrs.locking import LockProvider

_REENTRY_MESSAGE = (
    "FakeLockProvider: the current task already holds this lock key; "
    "locks are not reentrant (ADR-076)"
)

_TOKENS: ContextVar[dict[tuple[object, str], object]] = ContextVar(
    "pydomain_fake_lock_tokens", default={}
)
"""The hold tokens this context took, by provider and key (B-68).

Each value is replaced, never changed in place, so a task started from this
context keeps the tokens it saw when it started.
"""


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

    Any task may release a key, so the background releases that
    ``AggregateLockingBehavior`` hands over after a cancellation work.

    .. note::

       Lock entries accumulate over the process lifetime and are never
       evicted. For long-running processes or unbounded key spaces, use
       a distributed lock provider with TTL-based eviction.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._holds: dict[str, tuple[asyncio.Task[object] | None, object]] = {}
        self._identity = object()

    async def acquire(self, key: str) -> None:
        """Acquire the lock for *key*, creating one if necessary.

        Each acquire takes a new hold token and stores it in the calling
        context, where ``release`` finds it.

        Raises :class:`~pydomain.cqrs.exceptions.LockReentryError` if the
        current task already holds *key*. The message names no key.
        """
        # Safe under asyncio cooperative multitasking: no await between
        # check and assignment. Do NOT insert an await between these lines.
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        current = asyncio.current_task()
        if key in self._holds and self._holds[key][0] is current:
            raise LockReentryError(_REENTRY_MESSAGE)
        await self._locks[key].acquire()
        token = object()
        self._holds[key] = (current, token)
        _TOKENS.set({**_TOKENS.get(), (self._identity, key): token})

    async def release(self, key: str) -> None:
        """Release the hold on *key* that the calling context took.

        The context's token must match the current hold: the context that
        acquired, or a task started from it while it held the key. Any other
        release does nothing (B-68), so a late release, such as the retry
        ``AggregateLockingBehavior`` makes after an interrupted release,
        never frees a hold another holder took.

        Raises ``KeyError`` if no lock exists for *key*.
        """
        if key not in self._locks:
            msg = f"No lock registered for key: {key}"
            raise KeyError(msg)
        tokens = _TOKENS.get()
        mine = (self._identity, key)
        if key not in self._holds or self._holds[key][1] is not tokens.get(mine):
            return
        del self._holds[key]
        _TOKENS.set({k: v for k, v in tokens.items() if k != mine})
        self._locks[key].release()
