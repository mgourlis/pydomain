# How to Set Up Catch-Up Subscriptions

<!-- docs-test: runnable -->
<!-- docs-test: setup
from typing import ClassVar
from uuid import UUID, uuid4

from pydomain.ddd.domain_event import DomainEvent


class OrderPlaced(DomainEvent):
    order_id: UUID
    customer_id: UUID
    total_amount: int
    currency: str


class OrderCancelled(DomainEvent):
    order_id: UUID
-->

> **Adoption Level:** 5 · Prerequisites: [Subscriptions concept](../../concepts/es/subscriptions.md), [Track Checkpoints](../event-sourcing/track-checkpoints.md), [Bootstrap the Application](bootstrap-application.md)

This guide shows how to configure a `SubscriptionRunner` for durable catch-up subscriptions that keep projections in sync with the event store.

## 1. Define a concrete runner

Subclass `SubscriptionRunner` and implement `process_batch`:

```python
from pydomain.infrastructure.subscription import SubscriptionRunner, Subscription
from pydomain.es.checkpoint_store import CheckpointStore
from pydomain.es.event_store import EventStore


class ProjectionRunner(SubscriptionRunner):
    async def process_batch(self, events, subscription):
        for event in events:
            await subscription.projection.apply(event)
```

The runner handles checkpoint loading, global log polling, event filtering, and checkpoint persistence. Your `process_batch` implementation defines what to *do* with matching events.

## 2. Create projections and subscriptions

```python
from pydomain.es.projection import EventSourcedProjection
from pydomain.infrastructure.subscription import Subscription


class OrderSummaryProjection(EventSourcedProjection):
    name: ClassVar[str] = "order_summary"
    version: ClassVar[int] = 1

    def __init__(self) -> None:
        super().__init__()
        self.total_orders: int = 0
        self.total_revenue: int = 0

    async def _when_OrderPlaced(self, event: OrderPlaced) -> None:
        self.total_orders += 1
        self.total_revenue += event.total_amount

    async def _when_OrderCancelled(self, event: OrderCancelled) -> None:
        self.total_orders -= 1


projection = OrderSummaryProjection()

subscription = Subscription(
    subscription_id="order-summary",
    projection=projection,
    event_types=(OrderPlaced, OrderCancelled),
)
```

## 3. Wire up the runner

```python
from pydomain.testing.fake_event_store import FakeEventStore
from pydomain.testing.fake_checkpoint_store import FakeCheckpointStore

event_store = FakeEventStore()
checkpoint_store = FakeCheckpointStore()

runner = ProjectionRunner(
    event_store=event_store,
    checkpoint_store=checkpoint_store,
    poll_interval_seconds=1.0,        # Check for new events every second
    failure_backoff_seconds=0.1,      # Wait 100ms before retrying a failed batch
)

runner.add_subscription(subscription)
```

## 4. Start the runner

<!-- docs-test: skip -->
```python
import asyncio

async def main():
    # Seed events
    await event_store.append_to_stream("order-1", [
        OrderPlaced(order_id=..., customer_id=..., total_amount=1000, currency="EUR"),
    ], expected_version=0)

    # Run indefinitely
    await runner.run()

asyncio.run(main())
```

For test scenarios, seed the store and use `run_once()` to process a single batch:

```python
await event_store.append_to_stream(
    "order-1",
    [OrderPlaced(order_id=uuid4(), customer_id=uuid4(), total_amount=1000, currency="EUR")],
    expected_version=0,
)
await runner.run_once()  # Process one cycle, then return
assert projection.total_orders == 1
```

## 5. Graceful shutdown

```python
# In a shutdown handler
runner.stop()  # Current batch completes, then run() returns
```

`stop()` sets a flag that causes `run()` to exit after the current cycle. The currently processing batch finishes before the loop exits — no mid-batch interruption.

## 6. Bootstrap with the application

Wire the runner alongside the application for coordinated lifecycle:

<!-- docs-test: skip -->
```python
from pydomain.infrastructure.bootstrap import bootstrap


app = await bootstrap(
    event_store=event_store,
    message_bus=bus,
    snapshot_store=snapshot_store,
)

runner = ProjectionRunner(
    event_store=event_store,  # the same store passed to bootstrap()
    checkpoint_store=checkpoint_store,
)

# Start the runner as a background task
runner_task = asyncio.create_task(runner.run())
await asyncio.sleep(0)  # let run() start; it clears any stop request made before

# ... application runs ...

# Shutdown: the current batch finishes, then run() returns
runner.stop()
await runner_task
await app.shutdown()
```

## Expected outcome

A `SubscriptionRunner` that polls the event store's global log, dispatches matching events to projections, and persists checkpoints for durable progress tracking. The runner survives restarts — each projection picks up where it left off.

## Next steps

- [Catch-Up Subscriptions Recipe](../../how-to/recipes/subscriptions-catchup.md) — end-to-end pattern with a concrete runner
- [Publish Integration Events Recipe](../../how-to/recipes/publish-integration-events.md) — bridging to external brokers
- [Bootstrap the Application](bootstrap-application.md) — wiring the full application

## Cross-references

- **ADR-052**: Checkpoint store vs snapshot store
