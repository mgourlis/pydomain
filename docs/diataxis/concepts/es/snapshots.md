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
    state: dict[str, Any]        # The aggregate's fields from model_dump()
    schema_version: int = 1      # Aggregate's schema version
    created_at: datetime         # UTC timestamp
```

The `state` dict is produced by the aggregate's `_take_snapshot()` method, which calls `model_dump(by_alias=False, exclude_computed_fields=True)` on itself. This captures every field, keyed by its field name (never its alias), except `version` (which is stored separately) and the computed fields at every depth, which are derived from the other fields. Private attributes are never part of it.

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

`SnapshotThresholdPolicy.should_snapshot()` receives `current_version` and `pending_event_count`, the number of events in the batch just appended (not the number since the last snapshot). When `threshold=0`, it snapshots on every flush. Otherwise, it snapshots when `current_version % threshold == 0`.

### SnapshotSchemaPolicy — Is the snapshot still valid?

When an aggregate's schema changes (fields renamed, types changed), previously stored snapshots may be incompatible. The schema policy decides whether to use or reject a loaded snapshot:

```python
from pydomain.es.snapshot import RejectStaleSnapshotPolicy

# Reject snapshots whose schema_version doesn't match the aggregate
policy = RejectStaleSnapshotPolicy()
```

If rejected, the repository falls back to full event replay. Without a schema policy every snapshot is used, whatever its `schema_version`. The aggregate signals its schema version via the `_snapshot_schema_version` class variable:

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

A store keeps its snapshots to itself: changing a snapshot after `save()`, or one that `get()` returned, never changes what the store holds. A store that serializes snapshots does this by construction; an in-memory or caching store copies them. `SnapshotStoreContract` in `pydomain.testing.contracts` checks it.

## How the Repository Uses Snapshots

**On save:** After appending events, the repository evaluates `SnapshotPolicy.should_snapshot()`. If true, it calls `aggregate._take_snapshot()` and persists via `SnapshotStore.save()`.

**On load:** The repository tries the snapshot fast-path first:

1. Load snapshot from `SnapshotStore.get()`
2. Validate via `SnapshotSchemaPolicy.should_use_snapshot()` (or accept all if no policy configured)
3. If usable: validate `snapshot.state` into the aggregate class with `model_validate`, then replay events from `snapshot.version` onward
4. If not usable (rejected by the policy, or missing): full event stream replay from version 0

### Restoring by validation

Because the state is validated, not assigned attribute by attribute (ADR-075):

- Nested models come back as models and every typed list or dict is a new object, so a snapshot plus its tail gives the same aggregate as a full replay, and the snapshot itself is never changed by the replay. The state is validated by field name; a top-level key from an older snapshot that is a field's alias or serialization alias is renamed to the field name first, so aliased fields keep their values, even when an alias equals another field's name. State that gives one field under two keys (its name and an alias, or two aliases) is ambiguous and raises `SnapshotRestoreError` naming the field and the keys. Alias keys inside nested models are not renamed: an old one's value is dropped without an error and the nested field takes its default, so bump `_snapshot_schema_version` if old snapshots hold them.
- The aggregate's validators and `model_post_init` run on the restored state on every load. A validator with side effects runs each time.
- Private attributes start from their defaults. Derive them in `model_post_init` and keep them current in `_when`: `model_post_init` sees the restored fields on a snapshot load but the empty aggregate on a full replay, so an attribute derived only there depends on how the aggregate was loaded.
- Top-level keys of computed fields, under their names or aliases, are dropped before validation, so snapshots taken before computed fields were left out still load, even with `extra="forbid"`. A nested model's computed key is not dropped: an old snapshot holding one under that nested model's `extra="forbid"` raises `SnapshotRestoreError`, so bump `_snapshot_schema_version`. Keys of removed fields are ignored under the default `extra="ignore"`, and rejected under `extra="forbid"`.
- Values are validated, not compared by type: a value that still validates is coerced (a stored `"5"` loads into an `int` field), while a value that no longer validates, or a new required field without a default, raises.
- State that no longer validates raises `SnapshotRestoreError`, and so does any other exception your validators or `model_post_init` raise during the restore; the repository does not fall back to a replay. Its message names the failing locations (which include dict keys) and error codes, or the exception's type, never a value.

## Design decisions

> **📌 ADR-052**: Checkpoint store and snapshot store are separate protocols. Checkpoints track subscription progress (global event log position), snapshots capture aggregate state at a version. They serve different use cases and evolve independently.

> **📌 ADR-075**: A snapshot is restored with `model_validate`, computed fields are not snapshot state, and snapshot stores never share a snapshot object with their callers.

> **📌 ADR-043**: Snapshots are optional. An `EventSourcedRepository` without a `SnapshotStore` works correctly — it just replays the full event stream on every load. Add snapshots when replay latency becomes measurable.

## Common pitfalls

> **⚠️** **Bump `_snapshot_schema_version` after schema changes, and configure `RejectStaleSnapshotPolicy`.** If you add, rename or remove a field but don't bump the version, stale snapshots are loaded: a removed field's key is ignored (rejected under `extra="forbid"`), a new field takes its default, a compatible value is coerced, and a value that no longer validates, or a new required field without a default, raises `SnapshotRestoreError`.

> **⚠️** **Don't snapshot every event by default.** `SnapshotThresholdPolicy(threshold=10)` is a reasonable starting point. Setting `threshold=0` (snapshot every flush) trades write amplification for read speed — only do this when read latency is the bottleneck.

## Next steps

- [Configure Snapshots](../../how-to/event-sourcing/configure-snapshots.md) — wire snapshot store and policies
- [Event-Sourced Repositories](event-sourced-repositories.md) — how repositories use snapshots
- [Event Versioning](event-versioning.md) — schema evolution and upcasting
