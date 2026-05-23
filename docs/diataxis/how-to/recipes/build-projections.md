# Recipe: Build Projections

> **Adoption Level:** 4 · Prerequisites: [Projections concept](../../concepts/es/projections.md), [Event Store concept](../../concepts/es/event-store.md), [ES with CQRS recipe](es-with-cqrs.md)

This recipe shows how to build projections from an event stream with checkpoint tracking for durable catch-up subscriptions.

## Ingredients

- **Event store** — `FakeEventStore` with existing events
- **Projection** — extending `EventSourcedProjection`
- **Checkpoint store** — `FakeCheckpointStore`
- **Catch-up runner** — a simple loop reading from the global event log

## Step 1: Define a projection with multiple handlers

```python
from typing import ClassVar
from pydomain.es.projection import EventSourcedProjection


class OrderAnalyticsProjection(EventSourcedProjection):
    name: ClassVar[str] = "order_analytics"
    version: ClassVar[int] = 1

    def __init__(self) -> None:
        super().__init__()
        self.total_orders: int = 0
        self.cancelled_orders: int = 0
        self.revenue_by_currency: dict[str, int] = {}

    async def _when_OrderPlaced(self, event: OrderPlaced) -> None:
        self.total_orders += 1
        currency = event.currency
        self.revenue_by_currency[currency] = (
            self.revenue_by_currency.get(currency, 0) + event.total_amount
        )

    async def _when_OrderCancelled(self, event: OrderCancelled) -> None:
        self.cancelled_orders += 1

    async def rebuild(self, events: Sequence[DomainEvent]) -> None:
        self.total_orders = 0
        self.cancelled_orders = 0
        self.revenue_by_currency = {}
        await super().rebuild(events)
```

## Step 2: Seed the event store

```python
from pydomain.testing.fake_event_store import FakeEventStore

event_store = FakeEventStore()

# Simulate some events already in the store
await event_store.append_to_stream("order-1", [OrderPlaced(
    order_id=UUID(int=1), customer_id=UUID(int=10),
    total_amount=1000, currency="EUR", placed_at=datetime.now(UTC),
)], expected_version=0)

await event_store.append_to_stream("order-2", [OrderPlaced(
    order_id=UUID(int=2), customer_id=UUID(int=11),
    total_amount=500, currency="USD", placed_at=datetime.now(UTC),
)], expected_version=0)
```

## Step 3: Rebuild from scratch

```python
projection = OrderAnalyticsProjection()
global_stream = await event_store.read_all()

await projection.rebuild(global_stream.events)
assert projection.total_orders == 2
assert projection.revenue_by_currency == {"EUR": 1000, "USD": 500}
assert projection.checkpoint == global_stream.version  # Checkpoint matches
```

## Step 4: Catch up incrementally with checkpoint

```python
from pydomain.testing.fake_checkpoint_store import FakeCheckpointStore

checkpoint_store = FakeCheckpointStore()
projection = OrderAnalyticsProjection()

# Load last checkpoint
last_checkpoint = await checkpoint_store.load(projection.name)

# Read new events from that point
stream = await event_store.read_all(from_version=last_checkpoint)

# Apply and save checkpoint
for event in stream.events:
    await projection.apply(event)
await checkpoint_store.save(projection.name, projection.checkpoint)
```

## Step 5: Run a catch-up loop

The pattern for a subscription that polls for new events:

```python
import asyncio


async def catch_up_loop(
    projection: EventSourcedProjection,
    event_store: EventStore,
    checkpoint_store: CheckpointStore,
    interval: float = 1.0,
) -> None:
    while True:
        checkpoint = await checkpoint_store.load(projection.name)
        stream = await event_store.read_all(from_version=checkpoint)

        for event in stream.events:
            await projection.apply(event)

        if stream.events:
            await checkpoint_store.save(projection.name, projection.checkpoint)

        await asyncio.sleep(interval)
```

## What we built

A durable, checkpoint-tracked projection that can be rebuilt from scratch or catch up incrementally. The checkpoint store ensures that if the process restarts, it picks up where it left off — no events are missed or double-processed.

## Next steps

- [Build Denormalized Read Models recipe](build-denormalized-read-models.md) — publishing projections as read models
- [ES with CQRS recipe](es-with-cqrs.md) — the full write-side to read-side flow

## Cross-references

- **ADR-024**: Two separate projection types
- **ADR-025**: Projection split across layers
- **ADR-052**: Checkpoint store vs snapshot store
