# How to Implement a Query Handler

> **Prerequisites:** [Handlers concept](../../concepts/cqrs/handlers.md), [Read Models concept](../../concepts/cqrs/read-models.md)

## Problem

You need to implement the logic that fetches and projects data when a query is dispatched — querying a read store, transforming data, and returning a typed result.

## Solution

Create a class with an `async __call__(self, query) -> TResult` signature. Inject a read store via `__init__`. Register with the Query Bus.

## Steps

### 1. Create the read store protocol

```python
from typing import Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class OrderReadStore(Protocol):
    async def get(self, order_id: UUID) -> dict | None:
        """Fetch order data by ID."""
        ...

    async def find_by_customer(
        self, customer_id: UUID, limit: int, offset: int
    ) -> list[dict]:
        """Find orders for a customer."""
        ...
```

### 2. Create the handler class

```python
class GetOrderHandler:
    def __init__(self, read_store: OrderReadStore) -> None:
        self._read_store = read_store

    async def __call__(self, query: GetOrder) -> GetOrderResult:
        data = await self._read_store.get(query.order_id)
        if data is None:
            raise OrderNotFoundError(query.order_id)

        return GetOrderResult(
            order_id=data["order_id"],
            customer_name=data["customer_name"],
            total=data["total"],
            status=data["status"],
            items=[OrderLineProjection(**item) for item in data["items"]],
            placed_at=data["placed_at"],
        )
```

### 3. Register with the Query Bus

```python
from pydomain.cqrs.query_bus import QueryBus

bus = QueryBus()

bus.register(
    query_type=GetOrder,
    handler=GetOrderHandler(read_store),
)
```

## Read Store Injection

The read store is injected via `__init__`:

```python
class FindOrdersHandler:
    def __init__(self, read_store: OrderReadStore) -> None:
        self._read_store = read_store

    async def __call__(self, query: FindOrders) -> FindOrdersResult:
        orders = await self._read_store.find_by_customer(
            customer_id=query.customer_id,
            limit=query.limit,
            offset=query.offset,
        )
        return FindOrdersResult(
            orders=[OrderSummary(**o) for o in orders],
            total_count=len(orders),
        )
```

## No Unit of Work

Query handlers do **not** receive a UoW. Queries are read-only by contract:

```python
# Correct — no UoW
async def __call__(self, query: GetOrder) -> GetOrderResult:
    ...

# Wrong — queries don't get a UoW
async def __call__(self, query: GetOrder, uow: UnitOfWork) -> GetOrderResult:
    ...
```

## Error Handling

Raise domain-appropriate errors for not-found and invalid queries:

```python
from pydomain.ddd.exceptions import DomainError


class OrderNotFoundError(DomainError):
    """Raised when an order is not found."""


class GetOrderHandler:
    async def __call__(self, query: GetOrder) -> GetOrderResult:
        data = await self._read_store.get(query.order_id)
        if data is None:
            raise OrderNotFoundError(query.order_id)
        return GetOrderResult(**data)
```

## Stateless Handlers

Stateless handlers can be plain functions:

```python
async def handle_get_order(query: GetOrder) -> GetOrderResult:
    data = await read_store.get(query.order_id)
    if data is None:
        raise OrderNotFoundError(query.order_id)
    return GetOrderResult(**data)
```

## See Also

- [Handlers concept](../../concepts/cqrs/handlers.md)
- [Read Models concept](../../concepts/cqrs/read-models.md)
- [Define a Read Store Protocol](define-read-store-protocol.md)
- [Implement a Read Store](implement-read-store.md)
