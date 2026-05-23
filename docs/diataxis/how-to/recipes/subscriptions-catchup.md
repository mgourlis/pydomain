# Recipe: Catch-Up Subscriptions

> **Adoption Level:** 5 · Prerequisites: [Subscriptions concept](../../concepts/es/subscriptions.md), [Track Checkpoints](../event-sourcing/track-checkpoints.md), [ES with CQRS recipe](es-with-cqrs.md)

This recipe builds a complete catch-up subscription pipeline: a projection, a checkpoint-tracked runner, and a polling loop that keeps the projection in sync with the event store.

## Ingredients

- **Event store** — `FakeEventStore` seeded with events
- **Projection** — `EventSourcedProjection` subclass
- **Checkpoint store** — `FakeCheckpointStore`
- **SubscriptionRunner** — concrete subclass with `process_batch`

## Step 1: Define a projection

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

    async def rebuild(self, events):
        self.total_orders = 0
        self.cancelled_orders = 0
        self.revenue_by_currency = {}
        await super().rebuild(events)
```

## Step 2: Implement a concrete SubscriptionRunner

```python
from collections.abc import Sequence
from pydomain.infrastructure.subscription import SubscriptionRunner, Subscription


class AnalyticsRunner(SubscriptionRunner):
    async def process_batch(self, events, subscription):
        for event in events:
            await subscription.projection.apply(event)
```

## Step 3: Seed the event store

```python
from pydomain.testing.fake_event_store import FakeEventStore

event_store = FakeEventStore()

await event_store.append_to_stream("order-1", [
    OrderPlaced(order_id=UUID(int=1), customer_id=UUID(int=10),
                total_amount=1000, currency="EUR", placed_at=datetime.now(UTC)),
], expected_version=0)

await event_store.append_to_stream("order-2", [
    OrderPlaced(order_id=UUID(int=2), customer_id=UUID(int=11),
                total_amount=500, currency="USD", placed_at=datetime.now(UTC)),
], expected_version=0)

await event_store.append_to_stream("order-1", [
    OrderCancelled(order_id=UUID(int=1), cancelled_at=datetime.now(UTC)),
], expected_version=1)
```

## Step 4: Create the subscription and runner

```python
from pydomain.testing.fake_checkpoint_store import FakeCheckpointStore

projection = OrderAnalyticsProjection()
checkpoint_store = FakeCheckpointStore()

subscription = Subscription(
    subscription_id="order-analytics",
    projection=projection,
    event_types=(OrderPlaced, OrderCancelled),
)

runner = AnalyticsRunner(
    event_store=event_store,
    checkpoint_store=checkpoint_store,
    poll_interval_seconds=1.0,
)
runner.add_subscription(subscription)
```

## Step 5: Process with run_once

```python
await runner.run_once()

assert projection.total_orders == 2
assert projection.cancelled_orders == 1
assert projection.revenue_by_currency == {"EUR": 1000, "USD": 500}
```

## Step 6: Verify checkpoint durability

```python
# Checkpoint was saved
checkpoint = await checkpoint_store.load("order-analytics")
assert checkpoint > 0

# New events arrive
await event_store.append_to_stream("order-3", [
    OrderPlaced(order_id=UUID(int=3), customer_id=UUID(int=12),
                total_amount=750, currency="EUR", placed_at=datetime.now(UTC)),
], expected_version=0)

# Runner resumes from checkpoint — only processes the new event
await runner.run_once()
assert projection.total_orders == 3
assert projection.revenue_by_currency["EUR"] == 1750
```

## Step 7: Run as a continuous loop

```python
import asyncio

async def main():
    # Start the polling loop as a background task
    loop_task = asyncio.create_task(runner.run())

    # Application runs...

    # Graceful shutdown
    runner.stop()
    await loop_task

asyncio.run(main())
```

## What we built

A durable catch-up subscription pipeline. The runner polls the global event log, dispatches matching events to the projection, and persists checkpoints. After a restart, the projection resumes from the last checkpoint — no events are missed, and idempotent handlers protect against duplicates.

## Next steps

- [Publish Integration Events recipe](publish-integration-events.md) — bridge catch-up subscriptions to external brokers
- [Build Projections recipe](build-projections.md) — the simpler manual checkpoint pattern
- [Configure MessageSubscriber](../../how-to/infrastructure/configure-message-subscriber.md) — receiving events from external brokers

## Cross-references

- **ADR-052**: Checkpoint store vs snapshot store
