"""In-memory repository for testing."""

from __future__ import annotations

from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.exceptions import (
    AggregateNotFoundError,
    ConcurrencyError,
    RepositoryError,
)
from pydomain.ddd.repository import Repository


class FakeRepository[T: AggregateRoot, TId](Repository[T, TId]):
    """In-memory repository for testing purposes.

    Stores aggregates in a ``dict`` keyed by ``aggregate.id`` and
    tracks seen aggregates in ``_seen`` for UoW event collection.

    Example::

        repo = FakeRepository[Order, UUID]()
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
        if aggregate.id in self._store:
            raise RepositoryError(f"Aggregate with id {aggregate.id!r} already exists.")
        self._store[aggregate.id] = aggregate
        self._seen.add(aggregate)

    async def get_by_id(self, id_: TId) -> T | None:
        return self._store.get(id_)

    async def update(self, aggregate: T) -> None:
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

    async def delete(self, id_: TId) -> None:
        self._store.pop(id_, None)

    async def track(self, aggregate: T) -> None:
        self._seen.add(aggregate)
