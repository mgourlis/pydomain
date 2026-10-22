# How to Configure Snapshots

> **Adoption Level:** 5 · Prerequisites: [Snapshots concept](../../concepts/es/snapshots.md), [Event-Sourced Repositories concept](../../concepts/es/event-sourced-repositories.md)

This guide shows how to add snapshot storage and policies to an event-sourced repository for faster aggregate loads.

## 1. Choose a snapshot store

Use `FakeSnapshotStore` for tests, or implement `SnapshotStore` for production:

```python
from pydomain.testing.fake_snapshot_store import FakeSnapshotStore

snapshot_store = FakeSnapshotStore()
```

For production, implement the `SnapshotStore` protocol:

```python
from pydomain.es.snapshot import SnapshotStore, Snapshot

class PostgresSnapshotStore:
    async def save(self, aggregate_type: str, snapshot: Snapshot) -> None:
        # UPSERT INTO snapshots (aggregate_type, aggregate_id, version, state, schema_version, created_at)
        ...

    async def get(self, aggregate_type: str, aggregate_id: str) -> Snapshot | None:
        # SELECT ... FROM snapshots WHERE aggregate_type = $1 AND aggregate_id = $2
        ...
```

A store that serializes the snapshot on `save()` and builds a new one on `get()` already keeps its snapshots isolated, as the `SnapshotStore` Protocol requires. If yours caches `Snapshot` objects in memory, store and return `snapshot.model_copy(deep=True)`. Run `SnapshotStoreContract` from `pydomain.testing.contracts` against your adapter to check it.

## 2. Choose a snapshot policy

`SnapshotThresholdPolicy` snapshots every N events:

```python
from pydomain.es.snapshot import SnapshotThresholdPolicy

# Every 10 events (default)
policy = SnapshotThresholdPolicy(threshold=10)

# Every event — use when replay latency must be minimal
policy = SnapshotThresholdPolicy(threshold=0)
```

For custom logic, implement `SnapshotPolicy`:

```python
import time

from pydomain.es.snapshot import SnapshotPolicy


class TimeBasedSnapshotPolicy:
    def __init__(self, interval_seconds: int = 300) -> None:
        self._interval = interval_seconds
        self._last_snapshot: dict[str, float] = {}

    def should_snapshot(self, aggregate_type, aggregate_id, current_version, pending_event_count):
        key = f"{aggregate_type}:{aggregate_id}"
        now = time.monotonic()
        if pending_event_count > 0 and now - self._last_snapshot.get(key, 0) > self._interval:
            self._last_snapshot[key] = now
            return True
        return False
```

## 3. Choose a schema policy (optional)

When your aggregate schema changes, `RejectStaleSnapshotPolicy` prevents stale snapshot hydration:

```python
from pydomain.es.snapshot import RejectStaleSnapshotPolicy

schema_policy = RejectStaleSnapshotPolicy()
```

Without a schema policy, all snapshots are accepted regardless of `schema_version`. This is fine during early development when the aggregate schema is stable.

## 4. Wire into the repository

Pass the store and policies to `EventSourcedRepository`:

```python
from pydomain.es.event_sourced_repository import EventSourcedRepository

repository = EventSourcedRepository(
    event_store=event_store,
    aggregate_cls=Order,
    snapshot_store=snapshot_store,
    snapshot_policy=SnapshotThresholdPolicy(threshold=50),
    snapshot_schema_policy=RejectStaleSnapshotPolicy(),
)
```

All three snapshot parameters are optional. Omit `snapshot_store` and the repository replays the full event stream on every load. A `snapshot_store` without a `snapshot_policy` never takes a snapshot: it can only read snapshots that something else wrote.

## 5. Bump `_snapshot_schema_version` on aggregate changes

When you add, remove, or rename a field on your event-sourced aggregate, bump the version:

```python
class Order(EventSourcedAggregateRoot[str]):
    _snapshot_schema_version: ClassVar[int] = 2  # Was 1, bumped for new 'tax_rate' field

    customer_id: UUID
    total_amount: Decimal
    tax_rate: Decimal  # New field
```

The `RejectStaleSnapshotPolicy` will reject snapshots with `schema_version=1`, forcing a full replay that populates `tax_rate` from historical events via upcasters.

Without the bump, or without the policy, an old snapshot is still validated into the new class: a removed field's key is ignored (rejected under `extra="forbid"`), a new field with a default takes it, and a compatible value is coerced (a stored `"5"` into an `int`). A value that no longer validates, or a new required field without a default, raises `SnapshotRestoreError`, with no fallback to replay.

A strict-mode aggregate (`model_config = ConfigDict(strict=True)`) behind a store that serializes snapshots to JSON fails to restore values such as `datetime`, `UUID`, `Enum`, `Decimal`, `tuple` and `set`: JSON brings them back as strings and lists, which strict mode refuses. Keep such aggregates in lax mode, or use a store that keeps Python types.

## 6. Keep restore-safe aggregates

The repository restores a snapshot with `model_validate` (ADR-075), so:

- your model validators and `model_post_init` run on every load from a snapshot; keep them free of side effects;
- private attributes start from their defaults; derive them in `model_post_init` and keep them current in `_when`, because on a full replay `model_post_init` runs on the empty aggregate;
- an exception your validators or `model_post_init` raise during the restore becomes `SnapshotRestoreError`, named by its type only;
- computed fields are not stored, at any depth, and are recomputed after the restore;
- aliased fields are stored under their field names and validated by name; top-level alias keys of older snapshots are renamed to field names (a field given under two keys raises `SnapshotRestoreError` as ambiguous), but alias or computed keys inside nested models are not, so bump `_snapshot_schema_version` if old snapshots hold them.

## Expected outcome

An `EventSourcedRepository` that loads aggregates from the most recent snapshot and replays only the tail events. When the aggregate schema changes, stale snapshots are automatically rejected and rebuilt from the full event stream.

## Next steps

- [Handle ES Errors](handle-es-errors.md) — deal with `StaleSnapshotError` and concurrency conflicts
- [Implement an Upcaster](implement-upcaster.md) — transform old events to the current schema

## Cross-references

- **ADR-075**: Restore snapshots by validation
- **ADR-043**: Snapshots are optional
- **ADR-053**: Snapshot schema version policy
- **ADR-052**: Checkpoint store vs snapshot store
