# ADR-053: Snapshot Schema Version Policy

## Status

Accepted

## Date

2026-05-22

## Context

Snapshots capture aggregate state at a point in time via `model_dump()`. When an aggregate's fields change (rename, type change, removal, new required field), previously saved snapshots become incompatible with the current code. Loading a stale snapshot and hydrating an aggregate from it can cause:

- `ValidationError` during deserialization if fields are missing or wrong types.
- Silent data corruption if the schema change is additive and the snapshot state is incomplete.
- Confusing errors deep in domain logic rather than at the snapshot boundary.

The library already provides `EventUpcaster` chains (ADR-042) to handle schema evolution for *events*. Snapshots, however, are derived state — they are always rebuildable from the event log. The safest approach for a stale snapshot is to discard it and replay from events.

The `Snapshot` model already carries a `version` field (the aggregate's event stream version), but this tracks *event position*, not *schema shape*. A separate mechanism is needed to track schema compatibility.

Additionally, the snapshot policy concern (ADR-043) addresses *when* to take snapshots (write-time). Schema validation is a separate concern: *whether a loaded snapshot is usable* (read-time). These two concerns should not be merged — Interface Segregation Principle applies.

## Decision

Introduce three coordinated additions:

### 1. `schema_version` field on `Snapshot`

```python
class Snapshot(BaseModel):
    aggregate_id: str
    version: int
    state: dict[str, Any]
    schema_version: int = 1          # NEW
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

Default is `1`, preserving backward compatibility with existing snapshots that lack this field (Pydantic supplies the default on deserialization).

### 2. `_snapshot_schema_version` ClassVar on `EventSourcedAggregateRoot`

```python
class EventSourcedAggregateRoot(EventSourcedAggregateMixin, AggregateRoot[UUID]):
    _snapshot_schema_version: ClassVar[int] = 1
```

Aggregates that undergo schema changes bump this value. The `_take_snapshot()` method passes it to the `Snapshot` constructor.

### 3. `SnapshotSchemaPolicy` Protocol + `RejectStaleSnapshotPolicy`

```python
@runtime_checkable
class SnapshotSchemaPolicy(Protocol):
    def should_use_snapshot(self, snapshot: Snapshot, expected_schema_version: int) -> bool: ...

class RejectStaleSnapshotPolicy(SnapshotSchemaPolicy):
    def should_use_snapshot(self, snapshot, expected_schema_version):
        return snapshot.schema_version == expected_schema_version
```

**Repository integration**: `EventSourcedRepository` accepts an optional `snapshot_schema_policy` constructor parameter. In `get_by_id()`, after loading a snapshot, the repository checks:

```python
def _snapshot_is_usable(self, snapshot: Snapshot) -> bool:
    if self._snapshot_schema_policy is None:
        return True  # backward compatible — no policy = accept all
    expected = self._aggregate_cls._snapshot_schema_version
    return self._snapshot_schema_policy.should_use_snapshot(snapshot, expected)
```

If the policy rejects the snapshot, the repository falls back to full event replay (as if no snapshot existed).

### 4. `StaleSnapshotError` exception

```python
class StaleSnapshotError(DomainError):
    def __init__(self, aggregate_id, snapshot_version, expected_version): ...
```

Available for users who want explicit error handling instead of silent fallback.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Merge schema policy into `SnapshotPolicy` | Different lifecycle: `SnapshotPolicy` is write-time (when to snapshot), schema policy is read-time (whether to use). ISP violation. |
| Auto-detect schema drift via `model_validate()` try/except | Fragile — couples snapshot validation to Pydantic internals. Silent failures on additive changes that don't break validation. No diagnostic information. |
| Delete stale snapshots automatically | Side effect during read path. The repository's job is reconstitution, not snapshot lifecycle management. Users may want to keep old snapshots for auditing. |
| Upcast snapshots (like events) | Snapshots are derived state and always rebuildable. Upcasting adds complexity for no benefit — replay from events is the canonical approach. |
| Global schema version (no per-aggregate ClassVar) | Different aggregates evolve independently. A global version would force all aggregates to bump together. |

## Consequences

### Positive

- **Automatic stale detection**: The repository silently falls back to event replay when a snapshot is stale — no errors, no data corruption.
- **Backward compatible**: Existing code works unchanged. `schema_version` defaults to `1`, and no policy configured means all snapshots are accepted.
- **Opt-in granularity**: Users can configure the policy per repository, and bump `_snapshot_schema_version` per aggregate type.
- **Separate from snapshot frequency**: `SnapshotPolicy` (when) and `SnapshotSchemaPolicy` (whether valid) remain independent — ISP respected.
- **Diagnostic error available**: `StaleSnapshotError` provides aggregate ID, snapshot version, and expected version for troubleshooting.

### Negative

- **Users must remember to bump `_snapshot_schema_version`**: When an aggregate's fields change, the developer must manually increment the ClassVar. Forgetting this means stale snapshots are silently accepted.
- **No automatic schema migration**: Unlike `EventUpcaster` chains for events, there is no mechanism to transform snapshot state. Stale snapshots are discarded, not repaired.

### Neutral

- The `RejectStaleSnapshotPolicy` performs exact version matching. Custom policies could implement range matching or best-effort validation if needed.
- Stale snapshots remain in the `SnapshotStore` until overwritten by a new snapshot. They are not deleted.

## References

- `src/pydomain/es/snapshot.py` — `Snapshot.schema_version`, `SnapshotSchemaPolicy`, `RejectStaleSnapshotPolicy`
- `src/pydomain/es/aggregate.py` — `EventSourcedAggregateRoot._snapshot_schema_version`, `_take_snapshot()`
- `src/pydomain/es/event_sourced_repository.py` — `snapshot_schema_policy` parameter, `_snapshot_is_usable()`
- `src/pydomain/es/exceptions.py` — `StaleSnapshotError`
- `tests/es/test_snapshot_schema_version.py` — 21 tests covering the full feature
- [ADR-043](ADR-043-snapshot-policy-pluggable-protocol.md) — Snapshot policy (write-time, when to snapshot)
- [ADR-042](ADR-042-event-upcaster-chain-cycle-detection.md) — Event upcaster chain for event schema evolution
- §11.2 — Event Schema Evolution risk (now partially mitigated for snapshots)
