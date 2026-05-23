# How to Define a Read Store Protocol

> **Prerequisites:** [Read Models concept](../../concepts/cqrs/read-models.md), [Implement a Query Handler](implement-query-handler.md)

## Problem

You need to define the interface that query handlers use to fetch projected data, so that handlers are decoupled from storage implementations.

## Solution

Define a `Protocol` class with the query methods your handlers need. Make it `@runtime_checkable` so handlers can use `isinstance` checks. Implementations are injectable at bootstrap time.

## Steps

### 1. Identify the query methods

What questions will query handlers ask? List them:

- Get a single order by ID
- Find orders by customer, with pagination
- Count orders by status

### 2. Define the protocol

```python
from typing import Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class OrderReadStore(Protocol):
    """Read-side interface for order queries."""

    async def get(self, order_id: UUID) -> dict | None:
        """Return order data as a dict, or None if not found."""
        ...

    async def find_by_customer(
        self,
        customer_id: UUID,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Return (orders, total_count) for a customer."""
        ...

    async def count_by_status(self, status: str) -> int:
        """Count orders with the given status."""
        ...
```

Return dicts or simple DTOs, not domain entities. The handler maps dict fields to result types.

### 3. Use the protocol in handlers

```python
class GetOrderHandler:
    def __init__(self, read_store: OrderReadStore) -> None:
        self._store = read_store

    async def __call__(self, query: GetOrder) -> GetOrderResult:
        data = await self._store.get(query.order_id)
        if data is None:
            raise OrderNotFoundError(query.order_id)
        return GetOrderResult(**data)
```

### 4. Provide implementations

```python
# PostgreSQL implementation
class PostgresOrderReadStore:
    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, order_id: UUID) -> dict | None:
        async with self._session_factory() as session:
            row = await session.execute(
                select(OrderProjection).where(OrderProjection.id == order_id)
            )
            result = row.scalar_one_or_none()
            return result.to_dict() if result else None
    # ...

# In-memory implementation for tests
class InMemoryOrderReadStore:
    def __init__(self) -> None:
        self._orders: dict[UUID, dict] = {}

    async def get(self, order_id: UUID) -> dict | None:
        return self._orders.get(order_id)
    # ...
```

## Protocol Design Conventions

**Return dicts, not domain objects.** Read stores are infrastructure, not domain. Dicts keep them decoupled.

**One protocol per aggregate/domain area.** `OrderReadStore`, `CustomerReadStore`, `ProductReadStore` — not one giant `ReadStore`.

**Keep methods focused.** Each method answers one question. Avoid generic `query(filter: dict)` methods — they leak SQL into the domain.

**Use `@runtime_checkable`.** Enables `isinstance` checks and better error messages.

## Composing Multiple Read Stores

A handler that needs data from multiple sources injects multiple protocols:

```python
class OrderDetailsHandler:
    def __init__(
        self,
        orders: OrderReadStore,
        customers: CustomerReadStore,
    ) -> None:
        self._orders = orders
        self._customers = customers

    async def __call__(self, query: GetOrderDetails) -> GetOrderDetailsResult:
        order_data = await self._orders.get(query.order_id)
        customer_data = await self._customers.get(order_data["customer_id"])
        return GetOrderDetailsResult(
            order=OrderSummary(**order_data),
            customer=CustomerSummary(**customer_data),
        )
```

## See Also

- [Read Models concept](../../concepts/cqrs/read-models.md)
- [Implement a Read Store](implement-read-store.md)
- [Implement a Query Handler](implement-query-handler.md)
- [Build Projections (Recipe)](../recipes/build-projections.md)
