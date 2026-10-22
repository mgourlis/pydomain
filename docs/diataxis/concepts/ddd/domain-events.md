# Domain Events

<!-- docs-test: runnable -->

<!-- docs-test: setup
from typing import ClassVar
from uuid import UUID, uuid4

from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.id_generator import IdGenerator
-->

> **Adoption Level:** 1 — Tactical DDD
> **Module:** `pydomain.ddd.domain_event`

## What is a Domain Event?

A **Domain Event** is an immutable record of a **fact that happened** in the domain. Events are named in **past tense** in the Ubiquitous Language — `OrderPlaced`, not `PlaceOrder`.

Events are the primary mechanism for communicating state changes across aggregates. An [Aggregate Root](aggregates.md) records events during command handling, and the [Unit of Work](../cqrs/unit-of-work.md) publishes them after a successful commit.

## The `DomainEvent` Base Class

<!-- docs-test: skip -->
```python
class DomainEvent(BaseModel):
    event_id: UUID = Field(default_factory=...)      # auto-generated UUIDv7
    occurred_at: datetime = Field(default_factory=...)  # UTC timestamp
    event_version: int = 1
    correlation_id: UUID | None = None
    causation_id: UUID | None = None

    model_config = ConfigDict(frozen=True)
```

| Field | Type | Purpose |
|-------|------|---------|
| `event_id` | `UUID` | Unique event identifier (UUIDv7, time-ordered) |
| `occurred_at` | `datetime` | When the event occurred (UTC) |
| `event_version` | `int` | Schema version for evolution |
| `correlation_id` | `UUID \| None` | Distributed tracing — links events in the same workflow |
| `causation_id` | `UUID \| None` | Distributed tracing — which event caused this one |

## Past Tense Naming

Events are named as facts that **have already happened**:

```python
# ✅ Correct — past tense
class OrderPlaced(DomainEvent):
    order_id: UUID
    total_amount: int

# ❌ Wrong — imperative
class PlaceOrder(DomainEvent): ...
```

This naming convention reflects the nature of events: they are historical records, not commands. You cannot "un-happen" an event.

## Carrying Business Intent

Events carry the **business reason** for the state change, not the entire entity state:

```python
# ✅ Carries business intent
class OrderPlaced(DomainEvent):
    order_id: UUID
    total_amount: int
    currency: str

# ❌ Carries entire entity state
class OrderChanged(DomainEvent):
    order: dict  # Don't do this
```

This keeps events meaningful and prevents bloated event payloads.

## How Events Flow

```
1. Aggregate method runs
2. Aggregate calls self._add_event(SomeEvent(...))
3. Event is buffered in _pending_events
4. UnitOfWork.commit() calls aggregate.pull_events()
5. UnitOfWork stamps events with correlation_id / causation_id
6. UnitOfWork publishes stamped events to the MessageBus
```

The aggregate never knows about commands, tracing, or the message bus. It just records facts.

## Tracing IDs and Immutability

The `correlation_id` and `causation_id` fields default to `None` because the aggregate has no access to the command context. The Unit of Work stamps these fields during `commit()` by calling `event.stamp()`:

```python
class InvoiceIssued(DomainEvent):
    invoice_id: UUID
    line_totals: list[int]


issued = InvoiceIssued(invoice_id=uuid4(), line_totals=[100, 250])
stamped = issued.stamp(correlation_id=uuid4(), causation_id=uuid4())

assert issued.correlation_id is None  # the original is unchanged
assert stamped.event_id == issued.event_id  # same fact, now traced
```

`stamp()` returns a **new frozen deep copy**, so the original event is never mutated, not even through a list
or dict in its payload ([ADR-011](../../../adr/ADR-011-domain-event-stamp-immutability.md),
[ADR-070](../../../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md)). By the time any event handler receives the event, both tracing IDs are populated.

`stamp()` validates only the two ids, each against its field's type and constraints. A UUID string is
converted to a `UUID`; any other value raises Pydantic's `ValidationError`. No model validator of the event runs,
as in 0.3.0, so an invariant over both ids never sees a half-stamped event:

```python
from pydantic import ValidationError

correlation_id = uuid4()
assert issued.stamp(
    correlation_id=str(correlation_id), causation_id=uuid4()
).correlation_id == correlation_id

try:
    issued.stamp(correlation_id="not-a-uuid", causation_id=uuid4())
except ValidationError:
    pass  # a non-UUID tracing id is rejected
else:
    raise AssertionError("stamp() accepted a non-UUID id")
```

## Event IDs

When an event is validated (the constructor, `model_validate()`, `model_validate_json()`), a missing `event_id`
comes from the event class's `_id_generator`. `DomainEvent.configure(id_generator=...)` sets the generator for
every event type without its own; a subclass can set its own, with an `_id_generator` class variable or
`Sub.configure(...)`, which only that type and its subclasses use ([ADR-078](../../../adr/ADR-078-cleanup-errors-stored-versions-and-per-class-configure.md)). `model_construct()` skips validation and
uses the base class's generator. A generated `event_id` counts as set, so `model_dump(exclude_unset=True)`
includes it:

```python
class FixedIdGenerator:
    def generate(self) -> UUID:
        return UUID("00000000-0000-4000-8000-000000000001")


class ShipmentDispatched(DomainEvent):
    _id_generator: ClassVar[IdGenerator[UUID]] = FixedIdGenerator()
    shipment_id: UUID


assert ShipmentDispatched(shipment_id=uuid4()).event_id == UUID(
    "00000000-0000-4000-8000-000000000001"
)
```

`Command` and `Query` work the same way for `command_id` and `query_id`.

## Events Fail Independently

One event handler's failure must not affect other handlers. This is critical for the publish-subscribe model:

```
OrderPlaced event dispatched to:
  ├── Handler A: Send confirmation email  ← succeeds
  ├── Handler B: Update inventory         ← fails (logged, not raised)
  └── Handler C: Generate invoice         ← succeeds (independent of B)
```

The [Message Bus](../infrastructure/message-bus.md) catches and logs per-handler exceptions without aborting the event queue.

## Event Versioning

The `event_version` field supports schema evolution. When an event's structure changes, you increment the version and provide an upcaster to transform old events to the new format on read. See [Event Versioning](../es/event-versioning.md) for the full picture.

## Next Steps

- **[Publish a Domain Event →](../../how-to/ddd/publish-domain-event.md)** — step-by-step guide
- **[Aggregates →](aggregates.md)** — where events are born
- **[Specifications →](specifications.md)** — composable business rules
