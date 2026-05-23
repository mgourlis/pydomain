# How to Implement a Repository

> **Prerequisites:** [Repositories concept](../../concepts/ddd/repositories.md), [Aggregates concept](../../concepts/ddd/aggregates.md)

## Problem

You need to persist and retrieve aggregate roots from storage.

## Solution

Implement the `Repository[T, TId]` protocol with four methods: `save()`, `get_by_id()`, `delete()`, and `pull_events()`:

```python
from pydomain.ddd.repository import Repository


class InMemoryOrderRepository(Repository[Order, UUID]):  # explicit protocol inheritance
    ...
```

> **Note:** `Repository` is a `typing.Protocol` — structural subtyping also works. Explicit inheritance is recommended for static type checking and clarity.

## Steps

### 1. Define the interface in the domain layer

The repository contract is expressed in domain language:

```python
# This is already provided by pydomain — just use the Protocol
from pydomain.ddd.repository import Repository
```

### 2. Implement in the infrastructure layer

```python
from uuid import UUID
from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.exceptions import ConcurrencyError
from pydomain.ddd.repository import Repository


class InMemoryOrderRepository(Repository[Order, UUID]):
    """In-memory implementation for testing and prototyping."""

    def __init__(self) -> None:
        self._store: dict[UUID, Order] = {}
        self._seen: list[Order] = []

    async def save(self, aggregate: Order, command_id: UUID | None = None) -> None:
        existing = self._store.get(aggregate.id)
        if existing is not None and existing.version != aggregate.version:
            raise ConcurrencyError(
                f"Expected version {aggregate.version}, "
                f"but found {existing.version}"
            )
        self._store[aggregate.id] = aggregate
        self._seen.append(aggregate)

    async def get_by_id(self, id_: UUID) -> Order | None:
        found = self._store.get(id_)
        if found is not None:
            self._seen.append(found)
        return found

    async def delete(self, id_: UUID) -> None:
        self._store.pop(id_, None)

    def pull_events(self) -> list[DomainEvent]:
        events: list[DomainEvent] = []
        for aggregate in self._seen:
            events.extend(aggregate.pull_events())
        self._seen.clear()
        return events
```

### 3. Define the ORM model (infrastructure layer)

The ORM model is a separate class from the domain `Order` aggregate. It maps database columns using modern SQLAlchemy 2.0 style:

```python
from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class OrderModel(Base):
    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    customer_id: Mapped[UUID] = mapped_column()
    total_amount: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(20))
    version: Mapped[int] = mapped_column()

    @classmethod
    def from_aggregate(cls, aggregate: Order) -> OrderModel:
        """Map domain aggregate → ORM model."""
        return cls(
            id=aggregate.id,
            customer_id=aggregate.customer_id,
            total_amount=aggregate.total_amount,
            status=aggregate.status,
            version=aggregate.version + 1,
        )

    def to_aggregate(self) -> Order:
        """Map ORM model → domain aggregate."""
        return Order(
            id=self.id,
            customer_id=self.customer_id,
            total_amount=self.total_amount,
            status=self.status,
            version=self.version,
        )
```

### 4. Implement with SQLAlchemy

```python
from pydomain.ddd.exceptions import ConcurrencyError
from sqlalchemy.ext.asyncio import AsyncSession


class SqlAlchemyOrderRepository(Repository[Order, UUID]):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._seen: list[Order] = []

    async def save(self, aggregate: Order, command_id: UUID | None = None) -> None:
        row = await self._session.get(OrderModel, aggregate.id)
        if row is not None and row.version != aggregate.version:
            raise ConcurrencyError("Version mismatch")
        model = OrderModel.from_aggregate(aggregate)
        if row is None:
            self._session.add(model)        # new aggregate → INSERT
        else:
            self._session.merge(model)       # existing → UPDATE (sync, no await)
        self._seen.append(aggregate)

    async def get_by_id(self, id_: UUID) -> Order | None:
        row = await self._session.get(OrderModel, id_)
        if row is None:
            return None
        aggregate = row.to_aggregate()
        self._seen.append(aggregate)
        return aggregate

    async def delete(self, id_: UUID) -> None:
        row = await self._session.get(OrderModel, id_)
        if row is not None:
            await self._session.delete(row)

    def pull_events(self) -> list[DomainEvent]:
        events: list[DomainEvent] = []
        for aggregate in self._seen:
            events.extend(aggregate.pull_events())
        self._seen.clear()
        return events
```

> **SQLAlchemy async note:** Not all `AsyncSession` methods are awaitable. `get()`, `execute()`, `delete()`, `commit()`, and `flush()` are async. `add()` and `merge()` are **synchronous** — they only stage changes; the actual I/O happens on the next `flush()` or `commit()`.

### 5. Use the repository

```python
# Create and save
order = Order(customer_id=customer_id, total_amount=5000)
order.submit()
await repo.save(order)

# Retrieve
found = await repo.get_by_id(order.id)
assert found is not None
assert found.status == "submitted"

# Collect events for the Unit of Work
events = repo.pull_events()
```

## Key Design Points

### `seen` tracking

Track every aggregate loaded or saved in `self._seen`. The Unit of Work calls `pull_events()` to collect events from all touched aggregates for stamping and publishing.

### Optimistic concurrency

`save()` checks the aggregate's `version` against the stored version. On mismatch, raise `ConcurrencyError`. The caller can retry by re-loading and reapplying the command.

### Domain language

The repository interface uses domain terms (`save`, `get_by_id`, `delete`), not persistence terms (`insert`, `select`, `execute_query`).

## Using the Built-in Fake

For tests, use the library-provided fake:

```python
from pydomain.testing.fake_repository import FakeRepository

repo = FakeRepository()
await repo.save(order)
found = await repo.get_by_id(order.id)
```

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Creating a repository for a non-aggregate entity | Only aggregate roots get repositories |
| Forgetting `seen` tracking | Track all loaded/saved aggregates for event collection |
| Calling `session.commit()` in the repository | The Unit of Work manages commits |
| Leaking SQL/persistence details into the interface | Keep the interface in domain language |

## See Also

- [Repositories concept](../../concepts/ddd/repositories.md)
- [Aggregates concept](../../concepts/ddd/aggregates.md)
- [Unit of Work concept](../../concepts/cqrs/unit-of-work.md)
