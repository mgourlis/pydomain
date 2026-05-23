# Snapshots

> **Adoption Level:** 5 — Advanced Event Sourcing
> **Module:** `pydomain.es.snapshot`
> **Prerequisites:** [Event Sourcing](event-sourcing.md), [Event-Sourced Aggregates](event-sourced-aggregates.md), [Event-Sourced Repositories](event-sourced-repositories.md)

## What is a Snapshot?

A **Snapshot** is a point-in-time capture of an aggregate's full state at a specific version. Instead of replaying hundreds of events from version 0, the repository loads the most recent snapshot and replays only the tail events that occurred after it.

```
Without snapshots:
  ┌─E1─E2─E3─...─E97─E98─E99─E100─┐ → replay 100 events

With snapshots (every 50):
  ┌─S50────────────────────E51─...─E100─┐ → load snapshot + replay 50 events
```

## The `Snapshot` Model

```python
from pydomain.es.snapshot import Snapshot

class Snapshot(BaseModel):
    aggregate_id: str            # Aggregate identity
    version: int                 # Aggregate version at snapshot time
    state: dict[str, Any]        # Full aggregate state from model_dump()
    schema_version: int = 1      # Aggregate's schema version
    created_at: datetime         # UTC timestamp
```

The `state` dict is produced by the aggregate's `_take_snapshot()` method, which calls `model_dump(mode="python")` on itself. This captures every field except `version` (which is stored separately).

## Snapshot Policies

Three protocols govern snapshot behaviour:

| Protocol | Question | Default |
|----------|----------|---------|
| `SnapshotPolicy` | When to take a snapshot? | — |
| `SnapshotSchemaPolicy` | Is a stored snapshot still usable? | Accept all |
| `SnapshotStore` | Where are snapshots persisted? | — |

### SnapshotPolicy — When to snapshot

```python
from pydomain.es.snapshot import SnapshotPolicy, SnapshotThresholdPolicy

# Every 10 events (default)
policy = SnapshotThresholdPolicy(threshold=10)

# Every event (threshold=0 → any pending events trigger snapshot)
policy = SnapshotThresholdPolicy(threshold=0)
```

`SnapshotThresholdPolicy.should_snapshot()` receives `current_version` and `pending_event_count`. When `threshold=0`, it snapshots on every flush. Otherwise, it snapshots when `current_version % threshold == 0`.

### SnapshotSchemaPolicy — Is the snapshot still valid?

When an aggregate's schema changes (fields renamed, types changed), previously stored snapshots may be incompatible. The schema policy decides whether to use or reject a loaded snapshot:

```python
from pydomain.es.snapshot import RejectStaleSnapshotPolicy

# Reject snapshots whose schema_version doesn't match the aggregate
policy = RejectStaleSnapshotPolicy()
```

If rejected, the repository falls back to full event replay. The aggregate signals its schema version via the `_snapshot_schema_version` class variable:

```python
class Order(EventSourcedAggregateRoot[str]):
    _snapshot_schema_version: ClassVar[int] = 2  # Bumped after schema change
```

### SnapshotStore — Where snapshots go

```python
from pydomain.es.snapshot import SnapshotStore

class SnapshotStore(Protocol):
    async def save(self, aggregate_type: str, snapshot: Snapshot) -> None: ...
    async def get(self, aggregate_type: str, aggregate_id: str) -> Snapshot | None: ...
```

The `aggregate_type` discriminator (e.g. `"Order"`) allows a single store to hold snapshots for multiple aggregate types.

## How the Repository Uses Snapshots

**On save:** After appending events, the repository evaluates `SnapshotPolicy.should_snapshot()`. If true, it calls `aggregate._take_snapshot()` and persists via `SnapshotStore.save()`.

**On load:** The repository tries the snapshot fast-path first:

1. Load snapshot from `SnapshotStore.get()`
2. Validate via `SnapshotSchemaPolicy.should_use_snapshot()` (or accept all if no policy configured)
3. If usable: hydrate aggregate from `snapshot.state`, then replay events from `snapshot.version` onward
4. If not usable (stale or missing): full event stream replay from version 0

## Design decisions

> **📌 ADR-052**: Checkpoint store and snapshot store are separate protocols. Checkpoints track subscription progress (global event log position), snapshots capture aggregate state at a version. They serve different use cases and evolve independently.

> **📌 ADR-053**: Snapshots are optional. An `EventSourcedRepository` without a `SnapshotStore` works correctly — it just replays the full event stream on every load. Add snapshots when replay latency becomes measurable.

## Common pitfalls

> **⚠️** **Bump `_snapshot_schema_version` after schema changes.** If you add/rename/remove a field on your aggregate but don't bump the version, stale snapshots will be loaded and produce incorrect aggregate state.

> **⚠️** **Don't snapshot every event by default.** `SnapshotThresholdPolicy(threshold=10)` is a reasonable starting point. Setting `threshold=0` (snapshot every flush) trades write amplification for read speed — only do this when read latency is the bottleneck.

## Next steps

- [Configure Snapshots](../../how-to/event-sourcing/configure-snapshots.md) — wire snapshot store and policies
- [Event-Sourced Repositories](event-sourced-repositories.md) — how repositories use snapshots
- [Event Versioning](event-versioning.md) — schema evolution and upcasting
