# Event-Sourced Projections

> **Adoption Level:** 4 — Event Sourcing
> **Module:** `pydomain.es.projection`
> **Prerequisites:** [Event Sourcing](event-sourcing.md), [CQRS Projections](../cqrs/read-models.md), [Event Store](event-store.md)

## What is an Event-Sourced Projection?

An **EventSourcedProjection** is a concrete base class for building read models from a versioned event stream. It extends the CQRS [Projection Protocol](../cqrs/read-models.md) with event-sourcing-specific concerns: integer checkpoint tracking and convention-based handler dispatch.

The distinction is architectural: `Projection[StateT]` in `cqrs` is a Protocol (a contract — "what is a projection?"), while `EventSourcedProjection` in `es` is an ABC (a mechanism — "how do I build one from an event stream?").

## The `EventSourcedProjection` ABC

```python
from pydomain.es.projection import EventSourcedProjection
from pydomain.ddd.domain_event import DomainEvent


class EventSourcedProjection(ABC):
    name: ClassVar[str]
    version: ClassVar[int]

    checkpoint: int  # property — the event version processed up to

    async def handle(self, event: DomainEvent) -> None: ...
    async def apply(self, event: DomainEvent) -> None: ...
    async def rebuild(self, events: Sequence[DomainEvent]) -> None: ...
```

## Convention-Based Handler Dispatch

`handle(event)` constructs the handler name as `_when_{EventTypeName}` and calls it if it exists. This eliminates the `isinstance` chain:

```python
class OrderSummaryProjection(EventSourcedProjection):
    name: ClassVar[str] = "order_summary"
    version: ClassVar[int] = 1

    def __init__(self) -> None:
        super().__init__()
        self.total_orders: int = 0
        self.total_revenue: Money = Money(amount=0, currency="EUR")

    async def _when_OrderPlaced(self, event: OrderPlaced) -> None:
        self.total_orders += 1
        self.total_revenue = self.total_revenue.add(Money(
            amount=event.total_amount,
            currency=event.currency,
        ))

    async def _when_OrderCancelled(self, event: OrderCancelled) -> None:
        self.total_orders -= 1
```

Events with no matching `_when_*` method are silently ignored — the projection only handles events it cares about.

## Checkpoint Tracking

Every call to `apply(event)` increments `self.checkpoint`. This tracks the last processed global event version and is used with a [CheckpointStore](event-sourced-repositories.md) for durable catch-up subscriptions:

```python
projection = OrderSummaryProjection()
assert projection.checkpoint == 0

await projection.apply(order_placed_event)
assert projection.checkpoint == 1

await projection.apply(order_cancelled_event)
assert projection.checkpoint == 2
```

## Rebuild

`rebuild(events)` resets the checkpoint to 0 and replays a full event sequence. Subclasses should override to also reset custom state fields:

```python
class OrderSummaryProjection(EventSourcedProjection):
    name: ClassVar[str] = "order_summary"
    version: ClassVar[int] = 1

    async def rebuild(self, events: Sequence[DomainEvent]) -> None:
        self.total_orders = 0
        self.total_revenue = Money(amount=0, currency="EUR")
        await super().rebuild(events)
```

## Design decisions

> **📌 ADR-024**: Two separate projection types — `Projection[StateT]` Protocol in `cqrs`, `EventSourcedProjection` ABC in `es`. They share no inheritance, preserving the strict layer dependency: `cqrs` must not import from `es`.

> **📌 ADR-025**: The `name` and `version` `ClassVar` attributes serve different purposes: `name` identities the projection for checkpoint lookups, `version` tracks the projection schema for migration.

> **📌 ADR-039**: Convention-based dispatch (`_when_EventName`) replaces manual `isinstance` chains, reducing boilerplate and making it obvious which events a projection handles.

## Relationship to other concepts

- **CQRS Projection Protocol** (`cqrs/projection.py`): defines the `apply`/`rebuild` contract without event-sourcing knowledge
- **CheckpointStore**: persists `checkpoint` for durable subscription catch-up
- **InMemoryProjectionStore** (testing): an in-memory projection store for testing projections

## Common pitfalls

> **⚠️** **Don't forget to reset custom state in `rebuild`.** The base class resets `checkpoint`, but your fields like `total_orders` or `total_revenue` need explicit reset in an override.

> **⚠️** **Hyphenated event class names.** `_when_OrderPlaced` matches `OrderPlaced`. If your event is `Order-Placed`, the handler name would be `_when_Order-Placed` — which is not a valid Python identifier. Use snake_case event class names.

## Next steps

- [How to create an ES projection](../../how-to/event-sourcing/create-es-projection.md) — step-by-step guide
- [Build Projections recipe](../../how-to/recipes/build-projections.md) — full projection pipeline
- [Event Versioning](event-versioning.md) — handling event schema changes
