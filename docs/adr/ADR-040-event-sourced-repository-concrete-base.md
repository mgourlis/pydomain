# ADR-040: EventSourcedRepository as Concrete Base Class (Not Abstract)

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

DDD prescribes that repositories are abstract interfaces in the domain layer, with concrete implementations in infrastructure. However, event-sourced repositories share a common pattern:

1. **Save**: Pull pending events, append to stream with optimistic concurrency, optionally snapshot.
2. **Load**: Read stream, optionally use snapshot, replay events onto aggregate.
3. **Collect events**: Drain collected events for the Unit of Work.

This pattern is identical across all event-sourced aggregates — the only variation is the aggregate class.

## Decision

`EventSourcedRepository` is a **concrete generic base class**, not an abstract interface:

```python
class EventSourcedRepository[T: EventSourcedAggregateRoot[Any], TId]:
    def __init__(
        self,
        event_store: EventStore,
        aggregate_cls: type[T],
        snapshot_store: SnapshotStore | None = None,
        snapshot_policy: SnapshotPolicy | None = None,
    ): ...

    async def save(self, aggregate: T, command_id: UUID | None = None) -> None: ...
    async def get_by_id(self, id_: TId) -> T | None: ...
    def pull_events(self) -> list[DomainEvent]: ...
```

Usage:

```python
order_repo = EventSourcedRepository(
    event_store=event_store,
    aggregate_cls=Order,
    snapshot_store=snapshot_store,
    snapshot_policy=SnapshotThresholdPolicy(threshold=10),
)
```

No subclassing needed — the generic parameter `T` binds the aggregate type at construction time.

**Key design decisions:**
- `aggregate_cls: type[T]` is injected (not hardcoded) — supports multiple aggregate types.
- `snapshot_store` and `snapshot_policy` are optional — repos work without snapshots.
- `pull_events()` drains the internal buffer for UoW integration.
- `save()` computes `expected_version = aggregate.version - len(events)` for optimistic concurrency.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Abstract base with subclass per aggregate | Boilerplate; identical `save`/`get_by_id` in every subclass; violates DRY |
| Generic function-based approach | No state management (event buffer); cannot integrate with UoW |
| No repository (direct EventStore access) | Leaks event-sourcing infrastructure into application handlers; no UoW integration |

## Consequences

### Positive

- Zero boilerplate — instantiate with aggregate class, get a full repository.
- Generic `T` parameter provides type-safe `get_by_id()` return type.
- Optional snapshot support — add when needed, no refactoring required.
- `pull_events()` integrates with UoW's `seen` aggregate tracking.

### Negative

- Not a traditional DDD abstract repository — but the `EventStore` Protocol is the true abstraction point.
- Constructor injection of `aggregate_cls` is required — cannot infer from generic parameter at runtime.

### Neutral

- The repository is in the `es` module, not `infrastructure` — it depends only on `EventStore` (a Protocol) and `SnapshotStore` (a Protocol), both of which are infrastructure abstractions.

## References

- `src/pydomain/es/event_sourced_repository.py` — `EventSourcedRepository[T, TId]`
- `src/pydomain/es/event_store.py` — `EventStore` Protocol
- `src/pydomain/es/snapshot.py` — `SnapshotStore`, `SnapshotPolicy` Protocols
