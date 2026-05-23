# ADR-052: `CheckpointStore` vs `SnapshotStore` — Two Separate Persistence Concerns

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Both `CheckpointStore` and `SnapshotStore` persist state for performance optimization, but they serve fundamentally different purposes:

| Concern | CheckpointStore | SnapshotStore |
|---------|----------------|---------------|
| **What** | Integer position in the global event stream | Full aggregate state (dict) |
| **Who** | SubscriptionRunner (projections) | EventSourcedRepository (aggregates) |
| **Why** | Resume reading from last processed event | Skip replaying events during hydration |
| **Granularity** | One integer per subscription | One dict per aggregate |
| **Layer** | ES → Infrastructure | ES → Infrastructure |

Conflating these into a single store would violate SRP and create confusion about what each method does.

## Decision

Two separate Protocol-based stores:

### `CheckpointStore` (in `es/checkpoint_store.py`)

```python
@runtime_checkable
class CheckpointStore(Protocol):
    async def load(self, subscription_id: str) -> int:
        """Return last processed global event version, or 0."""

    async def save(self, subscription_id: str, checkpoint: int) -> None:
        """Persist the checkpoint for a subscription."""
```

- Stores a single integer per subscription ID.
- Used by `SubscriptionRunner` to track position in the global event log.
- `load()` returns `0` for new subscriptions (start from beginning).

### `SnapshotStore` (in `es/snapshot.py`)

```python
@runtime_checkable
class SnapshotStore(Protocol):
    async def save(self, aggregate_type: str, snapshot: Snapshot) -> None:
        """Persist a snapshot for the given aggregate type."""

    async def get(self, aggregate_type: str, aggregate_id: str) -> Snapshot | None:
        """Retrieve the latest snapshot for an aggregate."""
```

- Stores a `Snapshot` (aggregate_id, version, state dict) per aggregate instance.
- Used by `EventSourcedRepository` to skip event replay during `get_by_id()`.
- Keyed by `(aggregate_type, aggregate_id)` — not subscription ID.

### `Snapshot` Value Object

```python
class Snapshot(BaseModel):
    aggregate_id: str
    version: int
    state: dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Single `PositionStore` for both | Violates SRP; checkpoint is an integer, snapshot is a complex dict; different access patterns |
| No CheckpointStore (recompute from projection state) | Slow; requires reading the entire event log to find position |
| No SnapshotStore (always replay) | Performance degradation for long-lived aggregates with many events |

## Consequences

### Positive

- Clear separation: `CheckpointStore` for stream position, `SnapshotStore` for aggregate state.
- Both are Protocols — any storage backend (Postgres, Redis, in-memory) satisfies them.
- `CheckpointStore` is trivially simple (two methods, integer values).
- `SnapshotStore` carries rich metadata (`aggregate_type`, `version`, `created_at`).

### Negative

- Two stores to configure and inject — slightly more setup.

### Neutral

- Both stores are optional — the system works without checkpoints (re-reads from 0) and without snapshots (full replay).

## References

- `src/pydomain/es/checkpoint_store.py` — `CheckpointStore` Protocol
- `src/pydomain/es/snapshot.py` — `SnapshotStore` Protocol, `Snapshot` model
- `src/pydomain/es/event_sourced_repository.py` — Uses `SnapshotStore` in `get_by_id()`
- `src/pydomain/infrastructure/subscription.py` — Uses `CheckpointStore` in `SubscriptionRunner`
