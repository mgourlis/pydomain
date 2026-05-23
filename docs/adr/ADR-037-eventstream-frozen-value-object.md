# ADR-037: EventStream as Frozen Value Object

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Event-sourced operations need to return both the events and the stream position (version) as a single unit. Without a structured container, callers would receive a tuple `(events, version)` which is:
- Unnamed — `events[0]` and `events[1]` are opaque.
- Mutable — lists can be modified after return.
- Not serializable via Pydantic — no `model_dump()` or validation.

## Decision

`EventStream` is a frozen Pydantic model (Value Object):

```python
class EventStream(BaseModel):
    events: Sequence[DomainEvent]
    version: int

    model_config = ConfigDict(frozen=True)
```

Key properties:
- **Immutable**: `frozen=True` prevents mutation after construction.
- **Typed**: `events: Sequence[DomainEvent]` and `version: int` are explicit.
- **Serializable**: Inherits Pydantic serialization via `model_dump()`.
- **Sequence, not list**: Uses `Sequence[DomainEvent]` (read-only protocol) instead of `list[DomainEvent]` — callers cannot append or remove events.

Used by `EventStore` methods:
- `read_stream()` returns `EventStream` — events + current version.
- `read_all()` returns `EventStream` — global events + total event count.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| `tuple[list[DomainEvent], int]` | Unnamed fields; mutable list; no serialization; no validation |
| `dataclass` | No built-in validation; mutable by default; no Pydantic integration |
| `NamedTuple` | Immutable but no validation; no Pydantic integration |

## Consequences

### Positive

- Immutable return type — event store results cannot be accidentally modified.
- Pydantic validation on construction — malformed streams are caught early.
- Clear contract: `EventStream.version` is the stream position, `EventStream.events` is the data.

### Negative

- `frozen=True` means creating a new EventStream for each read — negligible cost.

### Neutral

- `Sequence` (not `list`) signals read-only intent but does not enforce it at runtime.

## References

- `src/pydomain/es/event_stream.py` — `EventStream` class
- `src/pydomain/es/event_store.py` — `EventStore.read_stream()`, `EventStore.read_all()` return `EventStream`
