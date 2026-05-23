# How to Implement an Event-Sourced Repository

> **Adoption Level:** 4 · Prerequisites: [Event-Sourced Repositories concept](../../concepts/es/event-sourced-repositories.md), [Event Store concept](../../concepts/es/event-store.md), [Connect an Event Store](connect-event-store.md)

This guide shows you how to use `EventSourcedRepository` to load and save event-sourced aggregates.

## 1. Basic setup

```python
from uuid import UUID
from pydomain.es.event_sourced_repository import EventSourcedRepository
from pydomain.testing.fake_event_store import FakeEventStore


event_store = FakeEventStore()
repository = EventSourcedRepository[Order, UUID](
    event_store=event_store,
    aggregate_cls=Order,
)
```

## 2. Save an aggregate

After calling aggregate methods that produce events, save the aggregate:

```python
order = Order(id=UUID(int=1), customer_id=UUID(int=2))
order.add_item(OrderItem(product_id=UUID(int=3), name="Widget", price=Money(amount=1000, currency="EUR"), quantity=2))
order.place()

await repository.save(order, command_id=cmd.command_id)
```

`save()` drains pending events, computes the expected version, and appends to the event store with optimistic concurrency control.

## 3. Load an aggregate

```python
order = await repository.get_by_id(UUID(int=1))
if order is None:
    raise OrderNotFoundError(...)

assert order.status == "placed"
assert len(order.items) == 1
```

`get_by_id()` recreates the aggregate from its event stream. Returns `None` if no stream exists.

## 4. Set up snapshots for fast loading

When aggregates accumulate many events, replay becomes slow. Add snapshots to accelerate loading:

```python
from pydomain.testing.fake_snapshot_store import FakeSnapshotStore
from pydomain.es.snapshot import SnapshotThresholdPolicy, RejectStaleSnapshotPolicy


repository = EventSourcedRepository[Order, UUID](
    event_store=event_store,
    aggregate_cls=Order,
    snapshot_store=FakeSnapshotStore(),
    snapshot_policy=SnapshotThresholdPolicy(threshold=10),
    snapshot_schema_policy=RejectStaleSnapshotPolicy(),
)
```

With snapshots, the repository:
1. Loads the latest snapshot (fast — one query)
2. Restores aggregate state from the snapshot
3. Reads tail events from the snapshot version onward
4. Replays only the tail events (few — fast)

The `SnapshotThresholdPolicy(threshold=10)` takes a snapshot every 10 events. Lower threshold = faster reads but more writes. `threshold=0` snapshots on every flush.

## 5. Integrate with the Unit of Work

The repository buffers collected events for the Unit of Work to publish:

```python
# After save(), drain events for publishing:
events = repository.pull_events()

# The Unit of Work stamps tracing IDs and publishes:
for event in events:
    event.correlation_id = uow.correlation_id
    event.causation_id = uow.causation_id
await message_bus.publish(events)
```

## 6. Define a custom snapshot policy

If `SnapshotThresholdPolicy` doesn't fit your needs, implement the `SnapshotPolicy` Protocol:

```python
from pydomain.es.snapshot import SnapshotPolicy


class TimeBasedSnapshotPolicy(SnapshotPolicy):
    def __init__(self, interval: timedelta) -> None:
        self._interval = interval

    def should_snapshot(
        self, aggregate_type: str, aggregate_id: str,
        current_version: int, pending_event_count: int,
    ) -> bool:
        # Custom logic: snapshot if last snapshot is older than interval
        ...
```

## Expected outcome

A repository that persists aggregates as event streams, with optional snapshot support for faster loading. The repository integrates with the Unit of Work to publish events after successful persistence.

## Next steps

- [Create an ES Projection](create-es-projection.md) — build read models from events
- [Handle ES Errors](handle-es-errors.md) — deal with concurrency and stale snapshots

## Cross-references

- **ADR-043**: Snapshot policy as pluggable Protocol
- **ADR-053**: Snapshot schema version policy
