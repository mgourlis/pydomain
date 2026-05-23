# Event-Sourced Aggregates

> **Adoption Level:** 4 — Event Sourcing
> **Module:** `pydomain.es.aggregate`
> **Prerequisites:** [Event Sourcing](event-sourcing.md), [Aggregates](../ddd/aggregates.md)

## What is an Event-Sourced Aggregate?

An **EventSourcedAggregateRoot** is an aggregate whose state is built from a sequence of [Domain Events](../ddd/domain-events.md). Instead of mutating fields directly and then recording events, all state changes flow through a single method — `_apply(event)` — which both mutates state and records the event.

## `EventSourcedAggregateRoot[TId]`

```python
from pydomain.es.aggregate import EventSourcedAggregateRoot
from pydomain.ddd.domain_event import DomainEvent


class EventSourcedAggregateRoot[TId](AggregateRoot[TId]):
    _snapshot_schema_version: ClassVar[int] = 1
```

It extends `AggregateRoot[TId]` (inheriting identity, version, and the pending events buffer) and adds the `_apply` / `_when` / `_replay` pattern.

## The Apply/When Pattern

State mutation is split into two layers:

| Method | Responsibility | Records Event? | Increments Version? |
|--------|---------------|----------------|---------------------|
| `_apply(event)` | Entry point for new events | Yes (`_add_event`) | Yes |
| `_when(event)` | State mutation dispatch | No | No |
| `_replay(event)` | State rebuild from history | No | Yes |

### `_when(event)` — Define State Transitions

Subclasses implement `_when` to dispatch by event type:

```python
from pydomain.es.aggregate import EventSourcedAggregateRoot


class Order(EventSourcedAggregateRoot[UUID]):
    customer_id: UUID
    status: str = "draft"
    items: list[OrderItem] = []
    total: Money = Money(amount=0, currency="EUR")

    def _when(self, event: DomainEvent) -> None:
        if isinstance(event, OrderCreated):
            self.customer_id = event.customer_id
            self.status = "draft"
        elif isinstance(event, LineItemAdded):
            self.items.append(event.item)
            self.total = self.total.add(event.item.price)
        elif isinstance(event, OrderPlaced):
            self.status = "placed"
        else:
            raise ValueError(f"Unknown event: {event!r}")
```

### `_apply(event)` — Record an Event

Called by aggregate methods to both mutate state and record the event:

```python
class Order(EventSourcedAggregateRoot[UUID]):

    def add_item(self, item: OrderItem) -> None:
        if self.status != "draft":
            raise OrderNotModifiable("Cannot modify a placed order")
        self._apply(LineItemAdded(
            order_id=self.id,
            item=item,
        ))
```

`_apply` calls `_when(event)` to update fields, then `_add_event(event)` to buffer the event, and finally increments `self.version`.

### `_replay(event)` — Rebuild from History

During reconstitution, `_replay` calls `_when(event)` and increments `self.version` but does **not** buffer the event. This ensures that loading an aggregate from the event store does not produce new pending events.

```python
# Inside the repository during get_by_id():
for event in stream.events:
    aggregate._replay(event)  # Rebuild state without buffering
```

## Aggregate Methods vs. Event Handlers

Aggregate methods are the public API — they validate invariants and then call `_apply`:

```python
class Order(EventSourcedAggregateRoot[UUID]):

    def place(self) -> None:
        if self.status != "draft":
            raise OrderNotPlacable("Order is not in draft status")
        if not self.items:
            raise OrderNotPlacable("Cannot place an empty order")
        self._apply(OrderPlaced(
            order_id=self.id,
            placed_at=datetime.now(UTC),
        ))
```

The method enforces invariants **before** recording the event. This guarantees that every recorded event represents a valid state transition.

## Snapshot Schema Version

`_snapshot_schema_version` is a `ClassVar[int]` that tracks the aggregate's field layout. When aggregate fields change (rename, add, remove, type change), bump this version so that [snapshot policies](event-sourced-repositories.md) can detect stale snapshots and fall back to full replay.

```python
class Order(EventSourcedAggregateRoot[UUID]):
    _snapshot_schema_version: ClassVar[int] = 2  # Bumped after adding discount field
```

## Design decisions

> **📌 ADR-025**: The projection split across layers — `EventSourcedProjection` lives in `es`, `Projection` Protocol lives in `cqrs`. The same layering principle applies: ES aggregates extend DDD aggregates, keeping the DDD layer free of event-sourcing concerns.

## Relationship to other concepts

- **AggregateRoot** (parent class): provides identity, version, and the pending events buffer
- **Event Store**: persists the events produced by `_apply`
- **EventSourcedRepository**: loads/saves these aggregates via event streams
- **Domain Events**: the events that `_when` dispatches on

## Common pitfalls

> **⚠️** **Don't call `_apply` from `_when`.** `_when` is a pure state transition. It should only mutate fields — never call `_apply` recursively, as that would record duplicate events.

> **⚠️** **Unknown event types should raise, not silently pass.** A `_when` implementation that ignores unknown events hides bugs. Always include an `else: raise` clause.

## Next steps

- [Event-Sourced Repositories](event-sourced-repositories.md) — how to load and save
- [How to define an event-sourced aggregate](../../how-to/event-sourcing/event-sourced-aggregate.md) — step-by-step guide
- [Event Store](event-store.md) — the persistence protocol
