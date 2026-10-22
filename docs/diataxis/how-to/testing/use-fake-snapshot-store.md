# How to Use a Fake Snapshot Store

<!-- docs-test: runnable -->

> **Prerequisites:** [Snapshots concept](../../concepts/es/snapshots.md), [Fake Event Store](use-fake-event-store.md), [Testing philosophy](../../concepts/testing/testing-philosophy.md)

## Problem

You need to test snapshot-based aggregate loading and snapshot policies without a real snapshot store. Tests must verify that snapshots are taken when the policy says so, that a load from a snapshot gives the same aggregate as a full replay, and that stale snapshots are skipped.

## Solution

Use `FakeSnapshotStore` from `pydomain.testing`, an in-memory implementation of the `SnapshotStore` protocol keyed by `(aggregate_type, aggregate_id)`. It stores a deep copy on `save()` and returns a deep copy from `get()`, as the Protocol requires of every store ([ADR-075](../../../adr/ADR-075-restore-snapshots-by-validation.md)): changing a snapshot you saved, or one you got back, never changes what the store holds.

## Steps

### 1. Define an aggregate to test

The examples use a synthetic `Order` with a nested model, so they show that a snapshot restores nested state:

```python
from pydantic import BaseModel, Field

from pydomain.ddd import DomainEvent
from pydomain.es import EventSourcedAggregateRoot


class OrderPlaced(DomainEvent):
    order_id: str
    city: str


class ItemAdded(DomainEvent):
    order_id: str
    sku: str


class ShippingAddress(BaseModel):
    city: str = ""


class Order(EventSourcedAggregateRoot[str]):
    address: ShippingAddress = Field(default_factory=ShippingAddress)
    skus: list[str] = Field(default_factory=list)

    def place(self, city: str) -> None:
        self._apply(OrderPlaced(order_id=self.id, city=city))

    def add_item(self, sku: str) -> None:
        self._apply(ItemAdded(order_id=self.id, sku=sku))

    def _when(self, event: DomainEvent) -> None:
        if isinstance(event, OrderPlaced):
            self.address = ShippingAddress(city=event.city)
        elif isinstance(event, ItemAdded):
            self.skus.append(event.sku)
        else:
            raise ValueError(f"Unknown event: {type(event).__name__}")
```

### 2. Save and retrieve a snapshot

`_take_snapshot()` captures the aggregate's fields (not `version`, computed fields or private attributes); the store keeps a copy:

```python
from pydomain.testing import FakeSnapshotStore

store = FakeSnapshotStore()
order = Order(id="order-1")
order.place("Testville")
order.add_item("sku-1")

snapshot = order._take_snapshot()
await store.save("Order", snapshot)

loaded = await store.get("Order", "order-1")
assert loaded is not None
assert loaded.version == 2
assert loaded.state["skus"] == ["sku-1"]

loaded.state["skus"].append("sku-x")  # changes this copy only
assert (await store.get("Order", "order-1")).state["skus"] == ["sku-1"]
```

### 3. Wire it into an `EventSourcedRepository`

```python
from pydomain.es import EventSourcedRepository, SnapshotThresholdPolicy
from pydomain.testing import FakeEventStore

event_store = FakeEventStore()
snapshot_store = FakeSnapshotStore()
repo = EventSourcedRepository(
    event_store=event_store,
    aggregate_cls=Order,
    snapshot_store=snapshot_store,
    snapshot_policy=SnapshotThresholdPolicy(threshold=2),
)
```

`SnapshotThresholdPolicy(threshold=2)` snapshots after a `save()` that leaves the aggregate at a version divisible by 2. It looks at the version after the save, not at how many events the save appended, so a save that jumps from version 1 to 3 takes no snapshot.

On load, the repository asks the store first. If it finds a snapshot, and no `SnapshotSchemaPolicy` rejects it, it validates the snapshot's state into `Order` with `model_validate` and replays only the newer events. With no schema policy, every snapshot is used.

### 4. Test snapshot creation and loading

```python
import pytest

pytestmark = pytest.mark.anyio  # the async tests run on the anyio pytest plugin


async def test_snapshot_taken_at_the_threshold() -> None:
    event_store = FakeEventStore()
    snapshot_store = FakeSnapshotStore()
    repo = EventSourcedRepository(
        event_store=event_store,
        aggregate_cls=Order,
        snapshot_store=snapshot_store,
        snapshot_policy=SnapshotThresholdPolicy(threshold=2),
    )
    order = Order(id="order-2")
    order.place("Testville")
    await repo.save(order)  # version 1: no snapshot
    assert await snapshot_store.get("Order", "order-2") is None

    order.add_item("sku-1")
    await repo.save(order)  # version 2: snapshot taken
    snapshot = await snapshot_store.get("Order", "order-2")
    assert snapshot is not None
    assert snapshot.version == 2


async def test_load_from_snapshot_equals_full_replay() -> None:
    event_store = FakeEventStore()
    snapshot_store = FakeSnapshotStore()
    repo = EventSourcedRepository(
        event_store=event_store,
        aggregate_cls=Order,
        snapshot_store=snapshot_store,
        snapshot_policy=SnapshotThresholdPolicy(threshold=2),
    )
    order = Order(id="order-3")
    order.place("Testville")
    order.add_item("sku-1")
    await repo.save(order)  # version 2: snapshot taken
    order.add_item("sku-2")
    await repo.save(order)  # version 3: one event after the snapshot

    from_snapshot = await repo.get_by_id("order-3")
    replayed = await EventSourcedRepository(event_store, Order).get_by_id("order-3")

    assert from_snapshot is not None and replayed is not None
    assert from_snapshot.version == replayed.version == 3
    assert isinstance(from_snapshot.address, ShippingAddress)
    assert from_snapshot.model_dump() == replayed.model_dump()
    # Loading again gives the same aggregate: the stored snapshot is unchanged.
    again = await repo.get_by_id("order-3")
    assert again is not None and again.skus == ["sku-1", "sku-2"]
```

<!-- docs-test: check
await test_snapshot_taken_at_the_threshold()
await test_load_from_snapshot_equals_full_replay()
-->

### 5. Test that a stale snapshot is skipped

Give the aggregate a new `_snapshot_schema_version` and configure `RejectStaleSnapshotPolicy`; a snapshot with an older `schema_version` is skipped and the aggregate is rebuilt by full replay:

```python
from typing import ClassVar

from pydomain.es import RejectStaleSnapshotPolicy, Snapshot


class OrderV2(Order):
    _snapshot_schema_version: ClassVar[int] = 2


async def test_stale_snapshot_is_skipped() -> None:
    event_store = FakeEventStore()
    snapshot_store = FakeSnapshotStore()
    order = OrderV2(id="order-4")
    order.place("Testville")
    order.add_item("sku-1")
    await event_store.append_to_stream("order-4", order.pull_events(), 0)
    await snapshot_store.save(
        "OrderV2",
        Snapshot(aggregate_id="order-4", version=2, state={"skus": []}, schema_version=1),
    )
    repo = EventSourcedRepository(
        event_store=event_store,
        aggregate_cls=OrderV2,
        snapshot_store=snapshot_store,
        snapshot_schema_policy=RejectStaleSnapshotPolicy(),
    )

    loaded = await repo.get_by_id("order-4")

    assert loaded is not None
    assert loaded.skus == ["sku-1"]  # from the events, not the stale snapshot
```

<!-- docs-test: check
await test_stale_snapshot_is_skipped()
-->

### 6. Check your own snapshot store with the contract suite

`FakeSnapshotStore` passes `SnapshotStoreContract`; run the same suite against your adapter to check it keeps the same promises, including isolation:

<!-- docs-test: skip -->
```python
import pytest

from pydomain.testing.contracts import SnapshotStoreContract


class TestPostgresSnapshotStore(SnapshotStoreContract):
    @pytest.fixture
    def snapshot_store(self):
        return PostgresSnapshotStore(...)  # your adapter, fresh and empty
```

## Expected Outcome

Your tests use `FakeSnapshotStore` to check when snapshots are taken, that a load from a snapshot equals a full replay, and that stale snapshots are skipped. The fake behaves as the `SnapshotStore` Protocol requires of a real store: it never shares a snapshot object with its callers.

## See Also

- [Snapshots concept](../../concepts/es/snapshots.md)
- [Configure Snapshots](../event-sourcing/configure-snapshots.md)
- [Use a Fake Event Store](use-fake-event-store.md)
- [Testing philosophy](../../concepts/testing/testing-philosophy.md)
