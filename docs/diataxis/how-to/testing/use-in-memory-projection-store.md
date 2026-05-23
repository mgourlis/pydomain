# How to Use an In-Memory Projection Store

> **Prerequisites:** [Read Models concept](../../concepts/cqrs/read-models.md), [Define a Read Store Protocol](../cqrs/define-read-store-protocol.md), [Testing philosophy](../../concepts/testing/testing-philosophy.md)

## Problem

You need to test projection handlers and query handlers that depend on a `ProjectionStore` without a real database. Tests must save and load read model state and verify that projections update correctly in response to domain events.

## Solution

Use `InMemoryProjectionStore` from `pydomain.testing` — an in-memory implementation of the `ProjectionStore` protocol backed by a `dict[str, Any]`. No serialization round-trip.

## Steps

### 1. Import InMemoryProjectionStore

```python
from pydomain.testing import InMemoryProjectionStore
```

### 2. Create a store

```python
store = InMemoryProjectionStore()
```

### 3. Save and load projection state

```python
await store.save("orders-summary", {
    "total_orders": 42,
    "total_revenue": Decimal("9999.00"),
    "last_updated": datetime.now(timezone.utc),
})

state = await store.load("orders-summary")
assert state["total_orders"] == 42
```

### 4. Load returns None for unknown IDs

```python
state = await store.load("nonexistent")
assert state is None
```

### 5. Overwrite projection state

```python
await store.save("orders-summary", {"total_orders": 1})
await store.save("orders-summary", {"total_orders": 2})

state = await store.load("orders-summary")
assert state["total_orders"] == 2  # overwritten
```

### 6. Use in a projection handler

```python
class UpdateOrderSummaryProjection:
    def __init__(self, store: ProjectionStore) -> None:
        self._store = store

    async def __call__(self, event: OrderPlaced) -> None:
        current = await self._store.load("orders-summary") or {"total_orders": 0}
        current["total_orders"] += 1
        await self._store.save("orders-summary", current)


store = InMemoryProjectionStore()
handler = UpdateOrderSummaryProjection(store)

await handler(OrderPlaced(order_id=UUID("..."), customer_id="c1"))

summary = await store.load("orders-summary")
assert summary["total_orders"] == 1
```

### 7. Combine with query handler in tests

```python
class GetOrderSummaryHandler:
    def __init__(self, store: ProjectionStore) -> None:
        self._store = store

    async def __call__(self, query: GetOrderSummary) -> dict[str, Any]:
        result = await self._store.load("orders-summary")
        return result or {"total_orders": 0}


store = InMemoryProjectionStore()
projection = UpdateOrderSummaryProjection(store)
query_handler = GetOrderSummaryHandler(store)

# Simulate event processing
await projection(OrderPlaced(order_id=UUID("..."), customer_id="c1"))

# Query the read model
result = await query_handler(GetOrderSummary())
assert result["total_orders"] == 1
```

## Complete Example

```python
import pytest
from uuid import UUID
from decimal import Decimal

from pydomain.testing import InMemoryProjectionStore


class OrderProjection:
    def __init__(self, store: InMemoryProjectionStore) -> None:
        self._store = store

    async def project_order_placed(self, event: OrderPlaced) -> None:
        await self._store.save(f"order:{event.order_id}", {
            "order_id": str(event.order_id),
            "customer_id": event.customer_id,
            "status": "placed",
            "items": event.items,
        })

    async def project_order_cancelled(self, event: OrderCancelled) -> None:
        current = await self._store.load(f"order:{event.order_id}")
        if current:
            current["status"] = "cancelled"
            await self._store.save(f"order:{event.order_id}", current)


class TestInMemoryProjectionStore:
    @pytest.fixture
    def store(self) -> InMemoryProjectionStore:
        return InMemoryProjectionStore()

    @pytest.fixture
    def projection(self, store) -> OrderProjection:
        return OrderProjection(store)

    async def test_save_and_load(self, store):
        await store.save("key-1", {"value": 42})
        result = await store.load("key-1")
        assert result == {"value": 42}

    async def test_load_unknown_returns_none(self, store):
        result = await store.load("nonexistent")
        assert result is None

    async def test_overwrite(self, store):
        await store.save("key-1", {"version": 1})
        await store.save("key-1", {"version": 2})
        result = await store.load("key-1")
        assert result["version"] == 2

    async def test_multiple_projections_independent(self, store):
        await store.save("proj-a", {"data": "a"})
        await store.save("proj-b", {"data": "b"})

        assert (await store.load("proj-a"))["data"] == "a"
        assert (await store.load("proj-b"))["data"] == "b"

    async def test_projection_handler_updates_read_model(self, store, projection):
        await projection.project_order_placed(OrderPlaced(
            order_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
            customer_id="c1",
            items=[OrderItem("widget", 2)],
        ))

        state = await store.load("order:550e8400-e29b-41d4-a716-446655440000")
        assert state["status"] == "placed"
        assert state["customer_id"] == "c1"
        assert len(state["items"]) == 1

    async def test_projection_updates_existing_state(self, store, projection):
        order_id = UUID("550e8400-e29b-41d4-a716-446655440000")

        await projection.project_order_placed(OrderPlaced(
            order_id=order_id, customer_id="c1", items=[]
        ))
        await projection.project_order_cancelled(OrderCancelled(order_id=order_id))

        state = await store.load(f"order:{order_id}")
        assert state["status"] == "cancelled"
```

## Expected Outcome

Your tests use `InMemoryProjectionStore` to verify that projection handlers update read model state correctly, and that query handlers return the persisted state. The store is a simple `dict` — no serialization, no database, no schema management. Tests are fast and deterministic.

## See Also

- [Read Models concept](../../concepts/cqrs/read-models.md)
- [Define a Read Store Protocol](../cqrs/define-read-store-protocol.md)
- [Implement a Read Store](../cqrs/implement-read-store.md)
- [Testing philosophy](../../concepts/testing/testing-philosophy.md)
