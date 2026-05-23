# Recipe: Build Denormalized Read Models

> **Adoption Level:** 4 · Prerequisites: [Projections concept](../../concepts/es/projections.md), [Read Models concept](../../concepts/cqrs/read-models.md), [Build Projections recipe](build-projections.md)

This recipe shows how to build denormalized read models by combining events from multiple aggregate types into a single query-optimized view.

## Ingredients

- **Multiple projections** — one per aggregate type
- **In-memory projection store** — `InMemoryProjectionStore` for querying
- **Event store** — for replay
- **Denormalized view** — combining data from multiple projections

## Step 1: Define per-aggregate projections

```python
from typing import ClassVar
from pydomain.es.projection import EventSourcedProjection


class OrderProjection(EventSourcedProjection):
    name: ClassVar[str] = "order_projection"
    version: ClassVar[int] = 1

    def __init__(self) -> None:
        super().__init__()
        self.orders: dict[UUID, dict] = {}

    async def _when_OrderPlaced(self, event: OrderPlaced) -> None:
        self.orders[event.order_id] = {
            "order_id": str(event.order_id),
            "customer_id": str(event.customer_id),
            "status": "placed",
            "total_amount": event.total_amount,
            "currency": event.currency,
            "placed_at": event.placed_at.isoformat(),
        }

    async def _when_OrderCancelled(self, event: OrderCancelled) -> None:
        if event.order_id in self.orders:
            self.orders[event.order_id]["status"] = "cancelled"
```

```python
class CustomerProjection(EventSourcedProjection):
    name: ClassVar[str] = "customer_projection"
    version: ClassVar[int] = 1

    def __init__(self) -> None:
        super().__init__()
        self.customers: dict[UUID, dict] = {}

    async def _when_CustomerCreated(self, event: CustomerCreated) -> None:
        self.customers[event.customer_id] = {
            "customer_id": str(event.customer_id),
            "name": event.name,
            "email": event.email,
        }

    async def _when_CustomerEmailChanged(self, event: CustomerEmailChanged) -> None:
        if event.customer_id in self.customers:
            self.customers[event.customer_id]["email"] = event.new_email
```

## Step 2: Build from the event store

```python
from pydomain.testing.fake_event_store import FakeEventStore

event_store = FakeEventStore()
# ... events appended to store ...

order_projection = OrderProjection()
customer_projection = CustomerProjection()

global_stream = await event_store.read_all()
for event in global_stream.events:
    await order_projection.apply(event)
    await customer_projection.apply(event)
```

## Step 3: Denormalize into a query model

Combine data from multiple projections into a denormalized read model:

```python
class OrderReadModel:
    def __init__(
        self,
        order_projection: OrderProjection,
        customer_projection: CustomerProjection,
    ) -> None:
        self._order_projection = order_projection
        self._customer_projection = customer_projection

    def get_order_details(self, order_id: UUID) -> dict | None:
        order = self._order_projection.orders.get(order_id)
        if order is None:
            return None

        customer = self._customer_projection.customers.get(
            UUID(order["customer_id"])
        )
        return {
            **order,
            "customer_name": customer["name"] if customer else "Unknown",
            "customer_email": customer["email"] if customer else "",
        }

    def get_orders_by_customer(self, customer_id: UUID) -> list[dict]:
        customer_id_str = str(customer_id)
        return [
            order for order in self._order_projection.orders.values()
            if order["customer_id"] == customer_id_str
        ]

    def get_total_revenue(self) -> int:
        return sum(
            o["total_amount"] for o in self._order_projection.orders.values()
            if o["status"] == "placed"
        )
```

## Step 4: Query the denormalized view

```python
read_model = OrderReadModel(order_projection, customer_projection)

# Single order with customer details (denormalized)
details = read_model.get_order_details(UUID(int=1))
assert details["customer_name"] == "Alice"

# All orders for a customer
alice_orders = read_model.get_orders_by_customer(UUID(int=10))
assert len(alice_orders) == 3

# Aggregate query
total = read_model.get_total_revenue()
assert total == 2500
```

## Step 5: Keep projections in sync

As new events arrive, update both projections:

```python
# When a new event arrives:
await order_projection.apply(new_event)
await customer_projection.apply(new_event)

# The read model automatically reflects the latest state
details = read_model.get_order_details(order_id)
```

## What we built

A denormalized read model that joins data from multiple event-sourced projections. Queries like "get order with customer name" or "total revenue" are O(1) lookups — no joins, no aggregates at query time. The trade-off: the read model is eventually consistent, updated asynchronously from the event stream.

## Performance considerations

- **Memory**: In-memory projections keep all data in RAM. For large datasets, persist to a database instead.
- **Rebuild time**: Full rebuilds replay all events from scratch. Use snapshots or persistent projections for production.
- **Staleness**: The read model is eventually consistent. Tune the catch-up interval based on acceptable staleness for your use case.

## Next steps

- [Build Projections recipe](build-projections.md) — checkpoint-based catch-up
- [ES with CQRS recipe](es-with-cqrs.md) — the full write-side flow
- [All Modules Integration recipe](all-modules.md) — complete application wiring

## Cross-references

- **ADR-024**: Two separate projection types
- **ADR-025**: Projection split across layers
