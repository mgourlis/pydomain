# How to Use a Fake Snapshot Store

> **Prerequisites:** [Snapshots concept](../../concepts/es/snapshots.md), [Fake Event Store](use-fake-event-store.md), [Testing philosophy](../../concepts/testing/testing-philosophy.md)

## Problem

You need to test snapshot-based aggregate loading and snapshot policies without a real snapshot store. Tests must verify that snapshots are saved after the configured threshold and that stale snapshots are handled correctly.

## Solution

Use `FakeSnapshotStore` from `pydomain.testing` — an in-memory implementation of the `SnapshotStore` protocol keyed by `(aggregate_type, aggregate_id)`.

## Steps

### 1. Import FakeSnapshotStore

```python
from pydomain.testing import FakeSnapshotStore
```

### 2. Create a fake snapshot store

```python
store = FakeSnapshotStore()
```

### 3. Save a snapshot

```python
from pydomain.es.snapshot import Snapshot


snapshot = Snapshot(
    aggregate_id="order-1",
    aggregate_type="Order",
    version=10,
    state=order.model_dump(),
    schema_version=1,
)

await store.save(aggregate_type="Order", snapshot=snapshot)
```

### 4. Retrieve a snapshot

```python
loaded = await store.get(aggregate_type="Order", aggregate_id="order-1")
if loaded is not None:
    order = Order.model_validate(loaded.state)
    print(f"Loaded from snapshot at version {loaded.version}")
```

### 5. Test with EventSourcedRepository

The `EventSourcedRepository` uses the snapshot store automatically when configured:

```python
from pydomain.testing import FakeEventStore, FakeSnapshotStore
from pydomain.es.event_sourced_repository import EventSourcedRepository
from pydomain.es.snapshot import SnapshotThresholdPolicy


event_store = FakeEventStore()
snapshot_store = FakeSnapshotStore()

repo = EventSourcedRepository(
    event_store=event_store,
    aggregate_cls=Order,
    snapshot_store=snapshot_store,
    snapshot_policy=SnapshotThresholdPolicy(5),  # snapshot every 5 events
)
```

When loading, the repository checks the snapshot store first. If a snapshot exists and its `schema_version` matches, it loads from the snapshot and replays only the newer events. If the `schema_version` is stale, the snapshot is ignored and the full event stream is replayed.

### 6. Verify snapshot creation

```python
async def test_snapshot_created_after_threshold():
    event_store = FakeEventStore()
    snapshot_store = FakeSnapshotStore()
    repo = EventSourcedRepository(
        event_store=event_store,
        aggregate_cls=Order,
        snapshot_store=snapshot_store,
        snapshot_policy=SnapshotThresholdPolicy(5),
    )

    order = Order.create(customer_id="c1", items=[])
    # Add items to generate enough events
    for i in range(6):
        order.add_item(OrderItem(f"product-{i}", 1))

    await repo.save(order)

    snapshot = await snapshot_store.get("Order", str(order.id))
    assert snapshot is not None
    assert snapshot.version >= 5
```

## Complete Example

```python
import pytest
from uuid import uuid4

from pydomain.testing import FakeEventStore, FakeSnapshotStore
from pydomain.es.event_sourced_repository import EventSourcedRepository
from pydomain.es.snapshot import SnapshotThresholdPolicy


class TestSnapshotStore:
    @pytest.fixture
    def event_store(self) -> FakeEventStore:
        return FakeEventStore()

    @pytest.fixture
    def snapshot_store(self) -> FakeSnapshotStore:
        return FakeSnapshotStore()

    @pytest.fixture
    def repo(self, event_store, snapshot_store):
        return EventSourcedRepository(
            event_store=event_store,
            aggregate_cls=Order,
            snapshot_store=snapshot_store,
            snapshot_policy=SnapshotThresholdPolicy(5),
        )

    async def test_loads_from_snapshot(self, event_store, snapshot_store, repo):
        # Create and save an aggregate with many events
        order = Order.create(customer_id="c1", items=[])
        for i in range(10):
            order.add_item(OrderItem(f"product-{i}", 1))
        await repo.save(order)

        # Clear the event store (simulate pruning) — snapshot remains
        event_store._store.clear()

        # Load — should restore from snapshot
        loaded = await repo.get_by_id(order.id)
        assert loaded is not None
        assert loaded.customer_id == "c1"
        assert loaded.version >= 10

    async def test_snapshot_stored_after_threshold(self, snapshot_store, repo):
        order = Order.create(customer_id="c1", items=[])
        for i in range(6):
            order.add_item(OrderItem(f"product-{i}", 1))
        await repo.save(order)

        snapshot = await snapshot_store.get("Order", str(order.id))
        assert snapshot is not None

    async def test_no_snapshot_below_threshold(self, snapshot_store, repo):
        order = Order.create(customer_id="c1", items=[])
        order.add_item(OrderItem("product-1", 1))  # only 1 event
        await repo.save(order)

        snapshot = await snapshot_store.get("Order", str(order.id))
        assert snapshot is None

    async def test_save_and_retrieve(self, snapshot_store):
        snapshot = Snapshot(
            aggregate_id="order-1",
            aggregate_type="Order",
            version=10,
            state={"customer_id": "c1", "items": []},
            schema_version=1,
        )

        await snapshot_store.save("Order", snapshot)
        loaded = await snapshot_store.get("Order", "order-1")

        assert loaded is not None
        assert loaded.version == 10
        assert loaded.state["customer_id"] == "c1"
```

## Expected Outcome

Your tests use `FakeSnapshotStore` to verify snapshot-based aggregate loading. Snapshots are stored and retrieved in memory, snapshot policies are tested, and the `EventSourcedRepository` integrates with the fake store exactly as it would with a real one.

## See Also

- [Snapshots concept](../../concepts/es/snapshots.md)
- [Use a Fake Event Store](use-fake-event-store.md)
- [Testing philosophy](../../concepts/testing/testing-philosophy.md)
