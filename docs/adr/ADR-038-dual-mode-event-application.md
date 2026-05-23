# ADR-038: Dual-Mode Event Application — `_apply()` Records, `_replay()` Reconstitutes

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

An event-sourced aggregate has two fundamentally different ways to process events:

1. **Recording new events**: When the aggregate performs a business action, it mutates state AND records the event for persistence. The event is buffered in `_pending_events` and the version is incremented.

2. **Reconstituting from history**: When loading from the event store, the aggregate replays past events to rebuild state. These events must NOT be buffered — they are already persisted. Only the version is incremented.

Using a single method for both would either buffer historical events (wrong) or skip version increments (also wrong).

## Decision

Two separate methods on `EventSourcedAggregateRoot`:

```python
class EventSourcedAggregateRoot[TId](AggregateRoot[TId]):

    def _apply(self, event: DomainEvent) -> None:
        """Record a NEW event: mutate state + buffer event + increment version."""
        self._when(event)
        self._add_event(event)      # Buffer for persistence
        self.version += 1

    def _replay(self, event: DomainEvent) -> None:
        """Replay a HISTORICAL event: mutate state + increment version only."""
        self._when(event)
        self.version += 1           # No buffering — event is already persisted
```

Both methods delegate state mutation to `_when(event)`:

```python
@abstractmethod
def _when(self, event: DomainEvent) -> None:
    """Subclass dispatches by event type using isinstance."""
    ...
```

**Usage in aggregates:**

```python
class Order(EventSourcedAggregateRoot[UUID]):
    def place(self, items: list[OrderLine]) -> None:
        event = OrderPlaced(order_id=self.id, items=items)
        self._apply(event)  # Records + mutates + increments version

    # Called by repository during reconstitution:
    # order._replay(event)  # Mutates + increments version (no buffering)
```

**Invariant**: After `_apply()`, `aggregate.version` equals the number of events applied. After `_replay()`, the version matches the stream length. `pull_events()` returns only `_apply`-generated events.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Single `_handle(event, is_new: bool)` | Boolean parameter is easy to misuse; both paths in one method is harder to reason about |
| Separate `RecordEvent` and `ReplayEvent` types | Wrapping every event in a container adds complexity with no benefit |
| Replay via constructor injection | Cannot replay incrementally (e.g., snapshot + tail events) |

## Consequences

### Positive

- Clear separation: `_apply()` is for new events, `_replay()` is for history.
- `_when()` is the single mutation point — no duplicated state-change logic.
- Version tracking is consistent: both methods increment version.
- `pull_events()` returns only new events (from `_apply`), never historical ones.

### Negative

- Two methods to learn (but each has a clear, distinct purpose).

### Neutral

- The repository calls `_replay()` during reconstitution — the aggregate subclass never calls `_replay()` directly.

## References

- `src/pydomain/es/aggregate.py` — `EventSourcedAggregateRoot._apply()`, `_replay()`, `_when()`
- `src/pydomain/es/event_sourced_repository.py` — Repository calls `_replay()` during `get_by_id()`
