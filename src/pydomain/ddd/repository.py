"""Repository protocol for DDD aggregate roots."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent

__all__ = [
    "Repository",
]


@runtime_checkable
class Repository[T: AggregateRoot[Any], TId](Protocol):
    """Repository protocol for aggregate roots.

    Defines the persistence contract for aggregate roots.  Only
    aggregate roots should have repositories.

    The contract is the same for both state-based and event-sourced
    persistence: ``save()`` upserts the aggregate and drains its
    pending domain events into an internal buffer.  The Unit of Work
    later retrieves those events via ``pull_events()`` for stamping
    and publishing.

    Usage::

        class OrderRepository(Repository[Order, UUID]):
            ...

        class SqlAlchemyOrderRepository(OrderRepository):
            ...
    """

    async def save(self, aggregate: T, command_id: UUID | None = None) -> None:
        """Persist an aggregate root (insert or update).

        Drains pending domain events from the aggregate and stores
        them in an internal buffer for later retrieval via
        ``pull_events()``.

        For new aggregates the implementation performs an INSERT.
        For existing aggregates it performs an UPDATE with an
        optimistic concurrency check on ``version``.

        Args:
            aggregate: The aggregate root to persist.
            command_id: Optional UUID identifying the command that
                triggered this save.  Event-sourced implementations
                use it for idempotency; state-based implementations
                may ignore it.

        Raises:
            ConcurrencyError: when the expected version does not match
                the currently stored version.
            DuplicateCommandError: when *command_id* is provided and
                the event store detects a duplicate submission
                (event-sourced implementations only).
        """
        ...

    async def get_by_id(self, id_: TId) -> T | None:
        """Retrieve an aggregate root by its identity, or ``None``."""
        ...

    async def delete(self, id_: TId) -> None:
        """Remove an aggregate root from the repository.

        Idempotent — does not raise if the aggregate does not exist.
        """
        ...

    def pull_events(self) -> list[DomainEvent]:
        """Drain and return collected domain events.

        Returns all events collected by ``save()`` since the last
        call to ``pull_events()``, then clears the internal buffer.

        The Unit of Work calls this during ``_collect_and_stamp()``
        to stamp events with correlation/causation IDs before
        publishing.
        """
        ...
