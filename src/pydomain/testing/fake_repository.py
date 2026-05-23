"""In-memory repository for testing."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.exceptions import ConcurrencyError
from pydomain.ddd.repository import Repository


class FakeRepository[T: AggregateRoot[Any], TId](Repository[T, TId]):
    """In-memory repository for testing purposes.

    Stores aggregates in a ``dict`` keyed by ``aggregate.id``.
    ``save()`` performs an upsert with optimistic concurrency
    checking and drains pending domain events into an internal
    buffer.  ``pull_events()`` drains that buffer for the Unit of
    Work to stamp and publish.

    Example::

        repo = FakeRepository[Order, UUID]()
        order = Order(id=uuid4())
        await repo.save(order)
        loaded = await repo.get_by_id(order.id)
    """

    def __init__(self, aggregates: list[T] | None = None) -> None:
        self._store: dict[object, T] = {}
        self._collected_events: list[DomainEvent] = []
        if aggregates is not None:
            for aggregate in aggregates:
                self._store[aggregate.id] = aggregate

    async def save(self, aggregate: T, command_id: UUID | None = None) -> None:
        """Upsert the aggregate and drain its pending events.

        Performs an INSERT when the aggregate is new (not in the store)
        or an UPDATE with optimistic concurrency checking when it already
        exists.

        Args:
            aggregate: The aggregate root to persist.
            command_id: Ignored by the fake (state-based) implementation.

        Raises:
            ConcurrencyError: when the stored version does not match the
                aggregate's version (optimistic concurrency conflict).
        """
        existing = self._store.get(aggregate.id)
        if existing is None:
            # INSERT
            self._store[aggregate.id] = aggregate
        else:
            # UPDATE with optimistic concurrency check
            if existing.version != aggregate.version:
                raise ConcurrencyError(
                    f"Version mismatch for aggregate {aggregate.id!r}: "
                    f"expected {aggregate.version}, found {existing.version}."
                )
            aggregate.version += 1
            self._store[aggregate.id] = aggregate
        self._collected_events.extend(aggregate.pull_events())

    async def get_by_id(self, id_: TId) -> T | None:
        """Return the aggregate with the given ID, or ``None``."""
        return self._store.get(id_)

    async def delete(self, id_: TId) -> None:
        """Remove an aggregate.  Idempotent — no error if not found."""
        self._store.pop(id_, None)

    def pull_events(self) -> list[DomainEvent]:
        """Drain and return collected domain events.

        Returns all events collected by ``save()`` since the last
        call to ``pull_events()``, then clears the internal buffer.
        """
        events = self._collected_events
        self._collected_events = []
        return events
