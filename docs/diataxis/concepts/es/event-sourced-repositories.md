# Event-Sourced Repositories

> **Adoption Level:** 4 — Event Sourcing
> **Module:** `pydomain.es.event_sourced_repository`
> **Prerequisites:** [Event Sourcing](event-sourcing.md), [Event-Sourced Aggregates](event-sourced-aggregates.md), [Event Store](event-store.md), [Repositories](../ddd/repositories.md)

## What is an Event-Sourced Repository?

An **EventSourcedRepository** loads and saves [Event-Sourced Aggregates](event-sourced-aggregates.md) via an [Event Store](event-store.md), with a different mechanism than a classic DDD repository:

- **DDD Repository**: persists the aggregate's current state directly (e.g., an ORM row)
- **ES Repository**: appends new events on save and reconstructs from the full event stream on load

## `EventSourcedRepository[T, TId]`

```python
from pydomain.es.event_sourced_repository import EventSourcedRepository
from pydomain.es.event_store import EventStore
from pydomain.es.snapshot import SnapshotStore, SnapshotPolicy, SnapshotSchemaPolicy


class EventSourcedRepository[T: EventSourcedAggregateRoot, TId]:
    def __init__(
        self,
        event_store: EventStore,
        aggregate_cls: type[T],
        snapshot_store: SnapshotStore | None = None,
        snapshot_policy: SnapshotPolicy | None = None,
        snapshot_schema_policy: SnapshotSchemaPolicy | None = None,
    ) -> None: ...
```

## `save(aggregate)` — Persist Events

```
┌────────────────────────────────────────────────┐
│ save(aggregate)                                 │
│                                                │
│ 1. pull_events() → drain pending events        │
│ 2. If no events, return (nothing to persist)   │
│ 3. expected_version = aggregate.version - len  │
│ 4. event_store.append_to_stream(...)           │
│ 5. If snapshot configured: evaluate policy     │
│    → maybe take & save snapshot               │
│ 6. Buffer events for Unit of Work pull        │
└────────────────────────────────────────────────┘
```

```python
# Inside the repository:
async def save(self, aggregate: T, command_id: UUID | None = None) -> None:
    events = aggregate.pull_events()
    if not events:
        return
    expected_version = aggregate.version - len(events)
    await self._event_store.append_to_stream(
        str(aggregate.id), events, expected_version, command_id=command_id
    )
    self._collected_events.extend(events)

    # Optionally snapshot
    if self._snapshot_store and self._snapshot_policy:
        if self._snapshot_policy.should_snapshot(...):
            snapshot = aggregate._take_snapshot()
            await self._snapshot_store.save(self.aggregate_type, snapshot)
```

## `get_by_id(id_)` — Load Aggregate

The repository uses a **snapshot-first** strategy (when configured):

```
┌────────────────────────────────────────────────┐
│ get_by_id(id_)                                  │
│                                                │
│ Snapshot path (preferred, fast):               │
│ 1. Load snapshot from SnapshotStore            │
│ 2. Validate snapshot schema version            │
│ 3. Create aggregate, restore state from snap   │
│ 4. Read tail events from snapshot.version      │
│ 5. Replay tail events → current aggregate      │
│                                                │
│ Full replay path (fallback):                   │
│ 1. Read full stream from version 0             │
│ 2. Create fresh aggregate                      │
│ 3. Replay all events → current aggregate      │
│                                                │
│ No stream found → return None                  │
└────────────────────────────────────────────────┘
```

## Snapshot System

Snapshots accelerate aggregate loading by saving the full state at a specific version, avoiding replay of the entire event stream.

### `Snapshot`

```python
from pydomain.es.snapshot import Snapshot


class Snapshot(BaseModel):
    aggregate_id: str
    version: int
    state: dict[str, Any]
    schema_version: int = 1
    created_at: datetime  # auto-set to UTC now
```

### `SnapshotPolicy` — When to Take a Snapshot

A Protocol that decides snapshot frequency:

```python
class SnapshotPolicy(Protocol):
    def should_snapshot(
        self,
        aggregate_type: str,
        aggregate_id: str,
        current_version: int,
        pending_event_count: int,
    ) -> bool: ...
```

Built-in: `SnapshotThresholdPolicy(threshold=10)` — snapshots every N events.

### `SnapshotStore` — Where Snapshots Live

A Protocol for snapshot persistence:

```python
class SnapshotStore(Protocol):
    async def save(self, aggregate_type: str, snapshot: Snapshot) -> None: ...
    async def get(self, aggregate_type: str, aggregate_id: str) -> Snapshot | None: ...
```

Use `FakeSnapshotStore` in tests.

### `SnapshotSchemaPolicy` — Stale Snapshot Detection

When aggregate fields change, old snapshots become incompatible. The schema policy detects this:

```python
class SnapshotSchemaPolicy(Protocol):
    def should_use_snapshot(
        self,
        snapshot: Snapshot,
        expected_schema_version: int,
    ) -> bool: ...
```

Built-in: `RejectStaleSnapshotPolicy` — rejects snapshots whose `schema_version` doesn't match the aggregate's `_snapshot_schema_version`. When rejected, the repository falls back to full event replay.

## `pull_events()` — Integration with Unit of Work

The repository buffers collected events and exposes them for the Unit of Work to stamp and publish:

```python
# After save():
events = repository.pull_events()  # [OrderPlaced, LineItemAdded, ...]
# Unit of Work stamps correlation/causation IDs and publishes via MessageBus
```

## Design decisions

> **📌 ADR-043**: Snapshot policy is a pluggable Protocol, not a fixed strategy. The default `SnapshotThresholdPolicy(threshold=10)` balances read performance and write amplification, but applications can implement custom policies (e.g., time-based, size-based).

> **📌 ADR-053**: Snapshot schema version tracking with `RejectStaleSnapshotPolicy` ensures that aggregate field changes don't silently corrupt hydration. If versions mismatch, the repository falls back to full replay.

## Relationship to other concepts

- **EventStore**: the source of truth for events
- **SnapshotStore**: accelerates aggregate loading
- **Unit of Work**: pulls collected events after `save()` and publishes them
- **Command Bus**: provides the `command_id` for idempotent event appends

## Common pitfalls

> **⚠️** **Snapshots are write-through, not write-behind.** The snapshot is taken during `save()` after a successful event append. This is simpler than async snapshotting and guarantees snapshot-event consistency, but adds latency to `save()`. Use the `SnapshotThresholdPolicy` to control frequency.

> **⚠️** **Forgetting to bump `_snapshot_schema_version`.** When you change aggregate fields, bump the schema version. Otherwise, `RejectStaleSnapshotPolicy` can't detect the incompatibility and may hydrate corrupt state from old snapshots.

## Next steps

- [How to implement an ES repository](../../how-to/event-sourcing/implement-es-repository.md) — step-by-step guide
- [How to connect an event store](../../how-to/event-sourcing/connect-event-store.md) — wiring guide
- [Projections](projections.md) — building read models from the event stream
