# Domain Events

> **Adoption Level:** 1 — Tactical DDD
> **Module:** `pydomain.ddd.domain_event`

## What is a Domain Event?

A **Domain Event** is an immutable record of a **fact that happened** in the domain. Events are named in **past tense** in the Ubiquitous Language — `OrderPlaced`, not `PlaceOrder`.

Events are the primary mechanism for communicating state changes across aggregates. An [Aggregate Root](aggregates.md) records events during command handling, and the [Unit of Work](../cqrs/unit-of-work.md) publishes them after a successful commit.

## The `DomainEvent` Base Class

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
def stamp(self, *, correlation_id: UUID, causation_id: UUID) -> DomainEvent:
    return self.model_copy(update={
        "correlation_id": correlation_id,
        "causation_id": causation_id,
    })
```

`stamp()` returns a **new frozen copy** — the original event is never mutated. By the time any event handler receives the event, both tracing IDs are populated.

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
