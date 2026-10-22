# ADR-011: DomainEvent `stamp()` Preserves Immutability

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Domain events are frozen Pydantic models (`frozen=True`) — they cannot be mutated after construction. However, the Unit of Work needs to add `correlation_id` and `causation_id` tracing IDs to events before publishing them. These IDs are not available when the aggregate records the event — the aggregate has no access to the command context.

The challenge: how to add tracing information to an immutable object without breaking immutability guarantees?

## Decision

`DomainEvent` provides a `stamp()` method that returns a **new frozen copy** with tracing IDs set, using Pydantic v2's `model_copy(update=...)`:

```python
class DomainEvent(BaseModel):
    event_id: UUID
    occurred_at: datetime
    event_version: int = 1
    correlation_id: UUID | None = None
    causation_id: UUID | None = None

    model_config = ConfigDict(frozen=True)

    def stamp(self, *, correlation_id: UUID, causation_id: UUID) -> DomainEvent:
        return self.model_copy(
            update={
                "correlation_id": correlation_id,
                "causation_id": causation_id,
            }
        )
```

> **Amended by [ADR-070](ADR-070-aggregate-copies-id-types-and-event-stamping.md)**. `stamp()` validates the two ids alone, each against its field's type and constraints (a UUID string is converted, anything else raises `pydantic.ValidationError`), then returns `model_copy(update={...}, deep=True)`. No model validator of the event runs, as before. The stamped event shares no list or dict with the original.

The workflow:
1. Aggregate records events via `_add_event()` — events have `correlation_id=None`, `causation_id=None`.
2. During `UnitOfWork.commit()`, the `_collect_and_stamp()` hook calls `stamp()` on each event.
3. The stamped copies (with tracing IDs) replace the originals in the event list.
4. By the time any event handler receives the event, both IDs are populated.

The original event is never modified — immutability is preserved.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Mutable events (`frozen=False`) | Violates event-sourcing principle: events are immutable facts; mutation would break audit trail |
| Set tracing IDs in the aggregate | Aggregate has no access to command context or tracing IDs; would couple domain layer to infrastructure |
| Separate `StampedEvent` wrapper type | Adds indirection; event handlers must unwrap; complicates serialization |
| `dataclasses.replace()` | Events are Pydantic models, not dataclasses; `model_copy` is the Pydantic v2 equivalent |

## Consequences

### Positive

- Events remain truly immutable — the original event is never modified.
- The aggregate is blissfully unaware of tracing infrastructure — no command context leaks into the domain layer.
- `model_copy(update=...)` is Pydantic v2's idiomatic way to create modified copies of frozen models.
- By the time handlers receive events, tracing IDs are always populated.

### Negative

- Creates a new event object per stamp — negligible memory cost for the typical 1-5 events per command.

### Neutral

- `stamp()` returns `Self` — subclasses preserve their concrete type through the stamp call. This was originally `DomainEvent` but was changed to `Self` in 2026-05 for consistency with `IntegrationEvent.stamp()`.

## References

- `src/pydomain/ddd/domain_event.py` — `DomainEvent.stamp()` method (returns `Self`)
- `src/pydomain/cqrs/integration_events.py` — `IntegrationEvent.stamp()` and `.stamp_from()` (mirrors this pattern)
- `src/pydomain/cqrs/unit_of_work.py` — `AbstractUnitOfWork._collect_and_stamp()` calls `stamp()` on each event
- ADR-005: Publish Events After Commit, Never Before
- ADR-061: Integration Event Distributed Tracing
