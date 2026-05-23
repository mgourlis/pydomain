# Repositories

> **Adoption Level:** 1 — Tactical DDD
> **Module:** `pydomain.ddd.repository`
> **Prerequisite:** [Aggregates](aggregates.md)

## What is a Repository?

A **Repository** provides the persistence contract for an [Aggregate Root](aggregates.md). It mediates between the domain and persistence layers, providing the illusion of an in-memory collection of aggregates.

Key rules:

1. **Only Aggregate Roots get repositories** — internal entities are persisted through the root
2. **Exactly one Repository per Aggregate Root type**
3. **Interface belongs to the domain layer**; implementation belongs to infrastructure
4. **Expressed in domain language**, not persistence language

## The `Repository[T, TId]` Protocol

```python
@runtime_checkable
class Repository[T: AggregateRoot[Any], TId](Protocol):
    async def save(self, aggregate: T, command_id: UUID | None = None) -> None: ...
    async def get_by_id(self, id_: TId) -> T | None: ...
    async def delete(self, id_: TId) -> None: ...
    def pull_events(self) -> list[DomainEvent]: ...
```

| Method | Purpose |
|--------|---------|
| `save(aggregate, command_id?)` | Persist an aggregate (insert or update) |
| `get_by_id(id_)` | Retrieve by identity, or `None` |
| `delete(id_)` | Remove — idempotent, no error if missing |
| `pull_events()` | Drain collected domain events |

## Structural Subtyping

`Repository` is a `typing.Protocol`, not an abstract base class. Any class with the matching methods **structurally conforms**:

```python
# ✅ Conforms to Repository[Order, UUID] — explicit inheritance recommended
class SqlAlchemyOrderRepository(Repository[Order, UUID]):
    async def save(self, aggregate: Order, command_id: UUID | None = None) -> None:
        ...
    async def get_by_id(self, id_: UUID) -> Order | None:
        ...
    async def delete(self, id_: UUID) -> None:
        ...
    def pull_events(self) -> list[DomainEvent]:
        ...
```

Explicit inheritance is recommended for static type checking and clarity, though structural subtyping also works. The protocol is `@runtime_checkable`, so you can verify conformance with `isinstance()` if needed.

## `save()` and Optimistic Concurrency

`save()` uses the aggregate's `version` field for optimistic concurrency:

```python
async def save(self, aggregate: Order, command_id: UUID | None = None) -> None:
    # For new aggregates: INSERT
    # For existing aggregates: UPDATE WHERE version = aggregate.version
    # On version mismatch: raise ConcurrencyError
```

The `command_id` parameter supports idempotency in event-sourced implementations. State-based implementations may ignore it.

## `seen` Tracking and Event Collection

Implementations track aggregates they've loaded or saved in a `seen` set. When `pull_events()` is called (by the Unit of Work), it drains events from all seen aggregates:

```python
from pydomain.ddd.repository import Repository


class InMemoryOrderRepository(Repository[Order, UUID]):
    def __init__(self) -> None:
        self._store: dict[UUID, Order] = {}
        self._seen: list[Order] = []

    async def save(self, aggregate: Order, command_id: UUID | None = None) -> None:
        self._store[aggregate.id] = aggregate
        self._seen.append(aggregate)

    async def get_by_id(self, id_: UUID) -> Order | None:
        found = self._store.get(id_)
        if found:
            self._seen.append(found)
        return found

    def pull_events(self) -> list[DomainEvent]:
        events: list[DomainEvent] = []
        for aggregate in self._seen:
            events.extend(aggregate.pull_events())
        self._seen.clear()
        return events
```

This pattern ensures the Unit of Work can collect all events from aggregates touched during a transaction.

## Domain Language, Not Persistence Language

The repository interface speaks in domain terms:

```python
# ✅ Domain language
await repo.save(order)
order = await repo.get_by_id(order_id)

# ❌ Persistence language leaking through
await repo.insert_into_orders_table(order)
order = await repo.execute_query("SELECT * FROM orders WHERE id = ?", order_id)
```

The interface is defined in the domain layer; the implementation (SQL, NoSQL, in-memory) lives in infrastructure.

## The Library Provides Fakes

The `pydomain.testing` module ships with `FakeRepository` for testing:

```python
from pydomain.testing.fake_repository import FakeRepository

repo = FakeRepository()
await repo.save(order)
found = await repo.get_by_id(order.id)
```

Use fakes in handler tests instead of mocking the repository.

## Next Steps

- **[Implement a Repository →](../../how-to/ddd/implement-repository.md)** — step-by-step guide
- **[Aggregates →](aggregates.md)** — what gets persisted
- **[Unit of Work →](../cqrs/unit-of-work.md)** — manages repository lifecycle
