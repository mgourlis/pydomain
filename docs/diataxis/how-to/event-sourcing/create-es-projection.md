# How to Create an Event-Sourced Projection

> **Adoption Level:** 4 · Prerequisites: [Projections concept](../../concepts/es/projections.md), [Event-Sourced Aggregates concept](../../concepts/es/event-sourced-aggregates.md)

This guide shows you how to build a read model by consuming domain events through an `EventSourcedProjection`.

## 1. Define the projection class

Subclass `EventSourcedProjection`, declare `name` and `version`, and add custom state fields:

```python
from typing import ClassVar
from pydomain.es.projection import EventSourcedProjection


class OrderSummaryProjection(EventSourcedProjection):
    name: ClassVar[str] = "order_summary"
    version: ClassVar[int] = 1

    def __init__(self) -> None:
        super().__init__()
        self.total_orders: int = 0
        self.total_revenue: Money = Money(amount=0, currency="EUR")
        self.orders_by_status: dict[str, int] = {"draft": 0, "placed": 0, "cancelled": 0}
```

## 2. Add `_when_*` handlers for events you care about

Each handler name follows the pattern `_when_{EventTypeName}`:

```python
class OrderSummaryProjection(EventSourcedProjection):

    async def _when_OrderPlaced(self, event: OrderPlaced) -> None:
        self.total_orders += 1
        self.total_revenue = self.total_revenue.add(
            Money(amount=event.total_amount, currency=event.currency)
        )
        self.orders_by_status["placed"] += 1

    async def _when_OrderCancelled(self, event: OrderCancelled) -> None:
        self.orders_by_status["cancelled"] += 1
        self.orders_by_status["placed"] -= 1
```

Events without a matching `_when_*` method are silently ignored — you only handle the events relevant to this projection.

## 3. Apply events individually

Use `apply(event)` for online processing (one event at a time):

```python
projection = OrderSummaryProjection()
assert projection.checkpoint == 0

await projection.apply(OrderPlaced(order_id=..., total_amount=1000, currency="EUR"))
assert projection.checkpoint == 1
assert projection.total_orders == 1
```

## 4. Rebuild from a full event stream

For catch-up or recovery, override `rebuild` to reset custom state before replaying:

```python
class OrderSummaryProjection(EventSourcedProjection):

    async def rebuild(self, events: Sequence[DomainEvent]) -> None:
        # Reset custom state first
        self.total_orders = 0
        self.total_revenue = Money(amount=0, currency="EUR")
        self.orders_by_status = {"draft": 0, "placed": 0, "cancelled": 0}
        # Replay all events (base class resets checkpoint)
        await super().rebuild(events)
```

Usage:

```python
# Load all events from the global store
stream = await event_store.read_all()
projection = OrderSummaryProjection()
await projection.rebuild(stream.events)

assert projection.total_orders > 0
assert projection.checkpoint == stream.version
```

## 5. Persist the checkpoint for durable subscriptions

Pair with a `CheckpointStore` for durable catch-up:

```python
from pydomain.testing.fake_checkpoint_store import FakeCheckpointStore

checkpoint_store = FakeCheckpointStore()
projection = OrderSummaryProjection()

# Load checkpoint
checkpoint = await checkpoint_store.load(projection.name)
stream = await event_store.read_all(from_version=checkpoint)

# Apply events
for event in stream.events:
    await projection.apply(event)

# Save checkpoint
await checkpoint_store.save(projection.name, projection.checkpoint)
```

## Expected outcome

A projection that builds a read model from domain events, with checkpoint tracking for durable catch-up subscriptions. The projection can be rebuilt from scratch at any time by replaying the full event stream.

## Next steps

- [Build Projections recipe](../../how-to/recipes/build-projections.md) — full projection pipeline
- [Implement an Upcaster](implement-upcaster.md) — handle event schema changes
- [Build Denormalized Read Models recipe](../../how-to/recipes/build-denormalized-read-models.md) — denormalized views

## Cross-references

- **ADR-024**: Two separate projection types
- **ADR-025**: Projection split across layers
- **ADR-039**: Convention-based handler dispatch
