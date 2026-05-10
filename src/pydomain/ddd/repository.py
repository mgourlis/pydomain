"""Repository protocol and implementations for DDD aggregate roots."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.exceptions import AggregateNotFoundError, ConcurrencyError

__all__ = [
    "FakeRepository",
    "Repository",
    "RepositoryError",
]


class RepositoryError(Exception):
    """Base class for repository-layer errors."""


@runtime_checkable
class Repository[T: AggregateRoot](Protocol):
    """Repository protocol for aggregate roots.

    Defines the persistence contract for aggregate roots.  Only
    aggregate roots should have repositories.  The protocol tracks
    ``_seen`` aggregates so the Unit of Work can collect and dispatch
    their pending domain events on commit.

    Usage::

        class OrderRepository(Repository[Order]):
            ...

        class SqlAlchemyOrderRepository(OrderRepository):
            ...
    """

    _seen: set[T]

    async def add(self, aggregate: T) -> None:
        """Persist a new aggregate root and register it as seen."""
        ...

    async def get_by_id(self, id_: object) -> T | None:
        """Retrieve an aggregate root by its identity, or ``None``."""
        ...

    async def update(self, aggregate: T) -> None:
        """Persist changes to an aggregate with optimistic concurrency check.

        Raises ``ConcurrencyError`` when the expected version does not
        match the currently stored version.
        """
        ...

    async def delete(self, id_: object) -> None:
        """Remove an aggregate root from the repository."""
        ...

    def track(self, aggregate: T) -> None:
        """Register an aggregate as seen without persisting.

        Used by the Unit of Work when an aggregate was loaded from the
        store and modified externally so its pending events can be
        collected on commit.
        """
        ...


class FakeRepository[T: AggregateRoot]:
    """In-memory repository for testing purposes.

    Stores aggregates in a ``dict`` keyed by ``aggregate.id`` and
    tracks seen aggregates in ``_seen`` for UoW event collection.

    Example::

        repo = FakeRepository[Order]()
        order = Order(id=uuid4())
        await repo.add(order)
        loaded = await repo.get_by_id(order.id)
    """

    def __init__(self, aggregates: list[T] | None = None) -> None:
        self._store: dict[object, T] = {}
        self._seen: set[T] = set()
        if aggregates is not None:
            for aggregate in aggregates:
                self._store[aggregate.id] = aggregate

    async def add(self, aggregate: T) -> None:
        """Register a new aggregate in the repository."""
        self._store[aggregate.id] = aggregate
        self._seen.add(aggregate)

    async def get_by_id(self, id_: object) -> T | None:
        """Retrieve an aggregate by its identity, or ``None``."""
        return self._store.get(id_)

    async def update(self, aggregate: T) -> None:
        """Update an existing aggregate with optimistic concurrency check.

        Raises ``AggregateNotFoundError`` if the aggregate is not found,
        or ``ConcurrencyError`` if the version has changed since the
        aggregate was loaded.
        """
        existing = self._store.get(aggregate.id)
        if existing is None:
            raise AggregateNotFoundError(
                f"Aggregate {aggregate.id!r} not found in repository."
            )
        if existing.version != aggregate.version:
            raise ConcurrencyError(
                f"Version mismatch for aggregate {aggregate.id!r}: "
                f"expected {aggregate.version}, found {existing.version}."
            )
        aggregate.version += 1
        self._store[aggregate.id] = aggregate
        self._seen.add(aggregate)

    async def delete(self, id_: object) -> None:
        """Remove an aggregate from the repository."""
        self._store.pop(id_, None)

    def track(self, aggregate: T) -> None:
        """Add an aggregate to the seen set for UoW event collection."""
        self._seen.add(aggregate)
