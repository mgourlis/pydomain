# ADR-050: AggregateRoot `_pending_events` as `PrivateAttr`

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

`AggregateRoot` is a Pydantic `BaseModel` (via `Entity[TId]`). It must collect domain events during command handling and expose them for dispatch after commit.

The design constraints:
1. Events must **not** appear in `model_dump()` / serialization — they are infrastructure metadata, not domain state.
2. Events must be mutable even if the aggregate is `frozen=True` — `list` is mutable regardless of model frozen state.
3. Events must be clearable — `pull_events()` drains the buffer.

## Decision

`_pending_events` uses Pydantic's `PrivateAttr`:

```python
class AggregateRoot[TId](Entity[TId]):
    _pending_events: list[DomainEvent] = PrivateAttr(default_factory=list)

    def _add_event(self, event: DomainEvent) -> None:
        self._pending_events.append(event)

    def pull_events(self) -> list[DomainEvent]:
        events, self._pending_events = self._pending_events, []
        return events
```

**Why `PrivateAttr`**:
- **Not serialized**: `model_dump()` excludes private attributes — events don't leak into persistence or API responses.
- **Mutable list**: Even with `frozen=True` on the model, the list itself is mutable (you can `append()` and reassign). Pydantic frozen mode prevents field reassignment, but `PrivateAttr` fields bypass this restriction because they are not model fields.
- **No validation**: Private attributes skip Pydantic validation — `DomainEvent` instances are stored directly without serialization overhead.

**Clearing**: `pull_events()` atomically swaps the list with a new empty list:

```python
events, self._pending_events = self._pending_events, []
```

This is safe because `list.__new__()` creates a fresh empty list, and the old list is returned to the caller.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Regular field `pending_events: list[DomainEvent] = []` | Appears in `model_dump()`; serialized into persistence; pollutes domain state |
| `@computed_field` | Cannot mutate from inside the model; read-only |
| Separate `EventCollector` class | Adds indirection; every aggregate must compose it; boilerplate |
| `__events` name-mangled attribute | Bypasses Pydantic entirely; no default_factory support; less idiomatic |

## Consequences

### Positive

- Events are invisible to serialization — clean domain model.
- `PrivateAttr` is the idiomatic Pydantic v2 way to store internal mutable state.
- `pull_events()` is atomic — no partial reads possible.
- Works with both `frozen=True` and `frozen=False` aggregates.

### Negative

- `PrivateAttr` is less discoverable than regular fields — IDE auto-complete may not show it.
- The underscore prefix `_pending_events` signals "private" but is not enforced by Python.

### Neutral

- Pydantic `PrivateAttr` fields are accessible via `model.__private_attributes__` if needed for debugging.

## References

- `src/pydomain/ddd/aggregate_root.py` — `AggregateRoot._pending_events`, `_add_event()`, `pull_events()`
- `src/pydomain/ddd/entity.py` — `Entity[TId]` base class
