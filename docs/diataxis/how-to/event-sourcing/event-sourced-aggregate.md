# How to Define an Event-Sourced Aggregate

> **Adoption Level:** 4 · Prerequisites: [Event-Sourced Aggregates concept](../../concepts/es/event-sourced-aggregates.md), [Domain Events concept](../../concepts/ddd/domain-events.md)

This guide shows you how to implement an aggregate whose state is built from an event stream using the `_when`/`_apply` pattern.

## 1. Define the domain events

Each state transition gets its own event type, named in past tense:

```python
from uuid import UUID
from datetime import datetime
from pydomain.ddd.domain_event import DomainEvent


class OrderCreated(DomainEvent):
    order_id: UUID
    customer_id: UUID


class LineItemAdded(DomainEvent):
    order_id: UUID
    item: OrderItem


class OrderPlaced(DomainEvent):
    order_id: UUID
    placed_at: datetime
```

## 2. Define the aggregate with `_when`

Subclass `EventSourcedAggregateRoot` and implement the `_when` dispatch:

```python
from uuid import UUID
from pydomain.es.aggregate import EventSourcedAggregateRoot
from pydomain.ddd.domain_event import DomainEvent


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

## 3. Add mutation methods that call `_apply`

Public methods validate invariants, then call `_apply(event)`:

```python
from pydomain.ddd.exceptions import DomainError


class OrderNotModifiable(DomainError): ...
class OrderNotPlacable(DomainError): ...


class Order(EventSourcedAggregateRoot[UUID]):
    # ... fields and _when as above

    def add_item(self, item: OrderItem) -> None:
        if self.status != "draft":
            raise OrderNotModifiable("Cannot modify a placed order")
        self._apply(LineItemAdded(
            order_id=self.id,
            item=item,
        ))

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

## 4. Override `_snapshot_schema_version` when needed

When aggregate fields change, bump the schema version so stale snapshots are detected:

```python
class Order(EventSourcedAggregateRoot[UUID]):
    _snapshot_schema_version: ClassVar[int] = 2  # Added discount_rate field
```

## Expected outcome

An aggregate that records every mutation as an event, enforces invariants through public methods, and can be reconstructed from its event stream by the repository.

## Next steps

- [Implement an ES Repository](implement-es-repository.md) — load and save your aggregate
- [Connect an Event Store](connect-event-store.md) — wire the persistence backend
- [Handle ES Errors](handle-es-errors.md) — concurrency, duplicates, stale snapshots

## Cross-references

- **ADR-025**: Projection split across layers — the layering principle behind the cqrs/es split
