"""Idempotency primitives for the CQRS pipeline.

Defines the ``ProcessedMessageStore`` Protocol (abstraction for
tracking processed message IDs), the optional ``MessageReservationStore``
Protocol (reserving a command before its handler runs), and the ``MISSING``
sentinel that distinguishes "never processed" from a cached ``None`` result.
"""

from __future__ import annotations

from collections.abc import Hashable
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


@runtime_checkable
class MessageReservationStore(Protocol):
    """Optional Protocol for stores that reserve a command before it runs.

    A :class:`ProcessedMessageStore` that also implements this Protocol lets
    :class:`~pydomain.cqrs.behaviors.IdempotencyBehavior` run one
    ``command_id`` at most once at a time: the behavior reserves the id
    before the handler, and a concurrent duplicate gets
    :class:`~pydomain.cqrs.exceptions.CommandInProgressError` (ADR-079
    point 4). A store without it keeps the ``get``/``set`` flow, where two
    concurrent duplicates can both run.

    Semantics every implementation keeps (``MessageReservationStoreContract``
    in ``pydomain.testing.contracts`` checks them):

    - ``reserve`` is atomic: of concurrent calls for one id, one gets a
      token and the others ``None``.
    - The token makes ``release`` ownership-safe: ``release(message_id,
      token)`` frees only the reservation that *token* took, never a newer
      one taken after it ended.
    - ``ProcessedMessageStore.set(message_id, result)`` completes the
      reservation; ``get`` then returns the result. An id that is only
      reserved is not a result: ``get`` returns ``MISSING`` for it.
    - Once completed, ``reserve`` returns ``None``, at least until the TTL,
      also after a ``release`` with the old token: a duplicate whose first
      ``get`` missed just before the completion finds the result instead of
      running again (B-142).
    - Reservations have a key space of their own, separate from results and
      from the marks of ``check_and_set``: reserving an id never marks it
      processed, and ``check_and_set`` never reserves it.
    - A reservation that is neither released nor completed (its process
      crashed, or its ``release`` failed) blocks the id until it expires.
      Give reservations a TTL longer than a command's handler and commit;
      nothing in pydomain enforces it.

    A store must work from any task. ``IdempotencyBehavior`` calls
    ``reserve`` and ``release`` through the same cancellation shield as the
    unit of work's ``_commit()``, which under asyncio runs the call in a
    task of its own (ADR-079). Build the store on a connection pool
    (``redis.asyncio``, an asyncpg pool, an ``AsyncEngine`` with a short
    session per call), never on the unit of work's task-scoped session.
    """

    async def reserve(self, message_id: UUID) -> Hashable | None:
        """Atomically reserve *message_id*; return a token, or ``None``.

        ``None`` means the id is already reserved, by a dispatch still
        running or by one whose reservation was completed with ``set``.
        """
        ...

    async def release(self, message_id: UUID, token: Hashable) -> None:
        """Drop the reservation *token* took, if it still holds *message_id*.

        A token whose reservation already ended (expired, released or
        completed) frees nothing, so a late release never drops another
        dispatch's reservation.
        """
        ...
