# ADR-043: Snapshot Policy as Pluggable Protocol

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Snapshots accelerate aggregate reconstitution by providing a saved state at a specific version, avoiding full event replay. But snapshot frequency is a trade-off:

- **Snapshot every event**: Maximum read performance, but high write amplification.
- **Snapshot every N events**: Balanced — good read performance with reasonable write cost.
- **Snapshot never**: No overhead, but long event streams are slow to replay.

The snapshot strategy depends on the aggregate type, event frequency, and read/write ratio. The repository should not hardcode a single strategy.

## Decision

Snapshot policy is a `Protocol` — any callable matching the signature is a valid policy:

```python
@runtime_checkable
class SnapshotPolicy(Protocol):
    def should_snapshot(
        self,
        aggregate_type: str,
        aggregate_id: str,
        current_version: int,
        pending_event_count: int,
    ) -> bool: ...
```

**Built-in implementation**: `SnapshotThresholdPolicy` snapshots every N events:

```python
class SnapshotThresholdPolicy(SnapshotPolicy):
    def __init__(self, threshold: int = 10):
        if threshold < 0:
            raise ValueError("threshold must be >= 0")
        self._threshold = threshold

    def should_snapshot(self, aggregate_type, aggregate_id, current_version, pending_event_count):
        if self._threshold == 0:
            return pending_event_count > 0  # Every flush
        return current_version % self._threshold == 0
```

**Integration**: The repository evaluates the policy after each successful event append:

```python
if self._snapshot_store is not None and self._snapshot_policy is not None:
    if self._snapshot_policy.should_snapshot(...):
        snapshot = aggregate._take_snapshot()
        await self._snapshot_store.save(self.aggregate_type, snapshot)
```

Both `snapshot_store` and `snapshot_policy` are optional constructor parameters — repos work without snapshots.

**`Snapshot` model**:

```python
class Snapshot(BaseModel):
    aggregate_id: str
    version: int
    state: dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

Captured via `aggregate._take_snapshot()` which serializes the full aggregate state via `model_dump(mode='python')`.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Hardcoded threshold in repository | Cannot customize per aggregate type; violates open/closed principle |
| Snapshot on every save (no policy) | High write amplification for frequently updated aggregates |
| Time-based snapshot (every N seconds) | Does not correlate with event frequency; may snapshot unchanged aggregates |

## Consequences

### Positive

- Pluggable: any object matching the Protocol is a valid policy.
- `SnapshotThresholdPolicy` covers the most common case (every N events).
- `threshold == 0` enables snapshot-every-flush for critical aggregates.
- Both `snapshot_store` and `snapshot_policy` are optional — opt-in.

### Negative

- Policy receives aggregate metadata, not the aggregate itself — cannot make decisions based on aggregate state.

### Neutral

- The snapshot policy is evaluated after `append_to_stream` succeeds — failed appends do not trigger snapshots.

## References

- `src/pydomain/es/snapshot.py` — `SnapshotPolicy` Protocol, `SnapshotThresholdPolicy`, `SnapshotStore` Protocol, `Snapshot` model
- `src/pydomain/es/event_sourced_repository.py` — Repository evaluates policy in `save()`
- `src/pydomain/es/aggregate.py` — `EventSourcedAggregateRoot._take_snapshot()`
