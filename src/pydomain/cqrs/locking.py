"""Locking abstractions for concurrency-safe message handling.

This module provides protocol types and lightweight implementations for
acquiring and releasing locks by message-derived keys.  It is **not** a
replacement for optimistic concurrency checks in the domain layer.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LockProvider(Protocol):
    """Protocol for acquiring and releasing named locks.

    Implementations must be safe for concurrent use (``asyncio`` tasks).
    """

    async def acquire(self, key: str) -> None:
        """Acquire the lock identified by *key*, blocking until held.

        An ``acquire`` cancelled while it waits never holds the key: once the
        holder releases, the key is free (ADR-076).

        Locks are not reentrant: acquiring a key the current task already
        holds is never granted. An implementation may raise (preferably
        :class:`~pydomain.cqrs.exceptions.LockReentryError`) or block, and
        must keep the first hold.
        """
        ...

    async def release(self, key: str) -> None:
        """Release the hold on *key* that the caller took.

        ``release`` must be ownership-safe, not just idempotent: a release of
        a hold that already ended, after another holder took the key, must
        not free the other holder's lock (ADR-076, B-68). It may do nothing
        or raise. ``AggregateLockingBehavior`` calls ``release`` again for a
        key whose release a cancellation interrupted, possibly after the
        first call took effect, and from a background task started from the
        caller's context. A provider ties each hold to its acquire with a
        per-acquire token: store it in a :class:`contextvars.ContextVar` in
        ``acquire`` (a task started later from that context sees it), and
        release only when the stored token still owns the key, for example
        with a compare-and-delete on the lock server.
        """
        ...


@runtime_checkable
class LockKeyResolver(Protocol):
    """Protocol for resolving lock keys from a message.

    Return an empty list to indicate that no locking is needed for the
    given message.
    """

    def resolve(self, message: Any) -> list[str]:
        """Return lock keys for *message*.

        An empty list means "do not lock."
        """
        ...


class DictLockKeyResolver:
    """A registry-based lock key resolver.

    Maps message types to key-extraction functions.  Resolving a message
    looks up all registered functions for the message's type and collects
    their returned keys.
    """

    def __init__(self) -> None:
        self._registry: dict[type, list[Callable[[Any], list[str]]]] = {}

    def register(
        self,
        message_type: type,
        key_fn: Callable[[Any], list[str]],
    ) -> None:
        """Register *key_fn* to be called for messages of *message_type*."""
        self._registry.setdefault(message_type, []).append(key_fn)

    def resolve(self, message: Any) -> list[str]:
        """Resolve lock keys for *message* from all registered functions."""
        keys: list[str] = []
        for key_fn in self._registry.get(type(message), ()):  # pyright: ignore[reportUnknownArgumentType]
            keys.extend(key_fn(message))
        return keys
