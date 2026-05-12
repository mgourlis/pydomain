"""Repository protocol for DDD aggregate roots."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydomain.ddd.aggregate_root import AggregateRoot

__all__ = [
    "Repository",
]


@runtime_checkable
class Repository[T: AggregateRoot, TId](Protocol):
    """Repository protocol for aggregate roots.

    Defines the persistence contract for aggregate roots.  Only
    aggregate roots should have repositories.

    Usage::

        class OrderRepository(Repository[Order, UUID]):
            ...

        class SqlAlchemyOrderRepository(OrderRepository):
            ...
    """

    async def add(self, aggregate: T) -> None:
        """Persist a new aggregate root and register it as seen.

        Raises ``RepositoryError`` if an aggregate with the same
        identity already exists.
        """
        ...

    async def get_by_id(self, id_: TId) -> T | None:
        """Retrieve an aggregate root by its identity, or ``None``."""
        ...

    async def update(self, aggregate: T) -> None:
        """Persist changes to an aggregate with optimistic concurrency check.

        On success, increments the aggregate's ``version`` in-place.

        Raises:
            AggregateNotFoundError: if no aggregate with the given identity
                exists in the repository.
            ConcurrencyError: when the expected version does not match the
                currently stored version.
        """
        ...

    async def delete(self, id_: TId) -> None:
        """Remove an aggregate root from the repository.

        Idempotent — does not raise if the aggregate does not exist.
        """
        ...

    async def track(self, aggregate: T) -> None:
        """Register an aggregate as seen without persisting.

        Used by the Unit of Work when an aggregate was loaded from the
        store and modified externally so its pending events can be
        collected on commit.
        """
        ...
