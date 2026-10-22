"""Idempotency primitives for the CQRS pipeline.

Defines the ``ProcessedMessageStore`` Protocol (abstraction for
tracking processed message IDs) and the ``MISSING`` sentinel that
distinguishes "never processed" from a cached ``None`` result.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from uuid import UUID

MISSING: Any = object()
"""Sentinel returned by :class:`ProcessedMessageStore` when no cached result exists."""


@runtime_checkable
class ProcessedMessageStore(Protocol):
    """Protocol for storage backends that track which message IDs have been processed.

    Used by :class:`~pydomain.cqrs.behaviors.IdempotencyBehavior` (commands
    cache results) and :class:`~pydomain.cqrs.event_bus.EventBus` (events
    check-and-set).

    Implementations should provide atomic check-and-set semantics to prevent
    races when the same message arrives concurrently.

    A store must work from any task. ``IdempotencyBehavior`` calls ``set``
    after a commit through the same cancellation shield as the unit of
    work's ``_commit()``, which under asyncio runs the call in a task of its
    own (ADR-079). Build the store on a connection pool (``redis.asyncio``,
    an asyncpg pool, an ``AsyncEngine`` with a short session per call), never
    on the unit of work's task-scoped session.
    """

    async def get(self, message_id: UUID) -> Any:
        """Return the cached result for *message_id*, or ``MISSING``.

        Used by the command idempotency path to return a previously cached
        ``CommandResult``.
        """
        ...

    async def set(self, message_id: UUID, result: Any) -> None:
        """Persist *result* for *message_id*.

        Used by the command idempotency path to cache a ``CommandResult``.
        """
        ...

    async def contains(self, message_id: UUID) -> bool:
        """Return ``True`` if *message_id* has already been processed."""
        ...

    async def check_and_set(self, message_id: UUID) -> bool:
        """Atomically check *message_id* and mark it as processed.

        Returns ``True`` if the ID was already processed (caller should
        skip duplicate handling), ``False`` if this is the first time.

        Used by the event idempotency path where no result caching is
        needed — the event is either consumed or skipped.
        """
        ...
