"""Tests for EventSourcedRepository via FakeEventSourcedRepository + FakeEventStore.

Covers saving (persisting events, multiple batches, concurrency),
loading (reconstitution, missing streams, clean state), and round-trip
scenarios with multiple aggregates.
"""

from __future__ import annotations

import inspect

import pytest

from pydomain.ddd.exceptions import ConcurrencyError
from pydomain.es.event_sourced_repository import EventSourcedRepository
from pydomain.es.snapshot import SnapshotThresholdPolicy
from pydomain.testing import FakeSnapshotStore
from pydomain.testing.fake_event_sourced_repository import FakeEventSourcedRepository
from pydomain.testing.fake_event_store import FakeEventStore
from tests.es.conftest import LineItemAdded, OrderCancelled, OrderPlaced, TestOrder

# ===================================================================
# save() -- Persisting pending events
# ===================================================================


class TestSave:
    """save() -- persisting pending events."""

    @pytest.mark.anyio
    async def test_save_persists_events_to_store(self) -> None:
        """After save, the event store contains the pending events from
        the aggregate."""
        store = FakeEventStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store, TestOrder
        )

        order = TestOrder(id="order-1")
        event = OrderPlaced(order_id="order-1", customer_name="Alice")
        order._apply(event)
        await repo.save(order)

        stream = await store.read_stream("order-1")
        assert len(stream.events) == 1
        assert isinstance(stream.events[0], OrderPlaced)
        assert stream.events[0].customer_name == "Alice"

    @pytest.mark.anyio
    async def test_save_with_no_pending_events_does_not_raise(self) -> None:
        """Calling save on an aggregate with no pending events is a no-op
        and does not raise."""
        store = FakeEventStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store, TestOrder
        )

        order = TestOrder(id="order-1")
        # No events applied -- nothing to save
        await repo.save(order)  # should not raise

    @pytest.mark.anyio
    async def test_save_appends_multiple_batches(self) -> None:
        """Events from successive save calls are appended to the same
        stream in order."""
        store = FakeEventStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store, TestOrder
        )

        order = TestOrder(id="order-1")
        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        await repo.save(order)

        order._apply(LineItemAdded(order_id="order-1", item_name="Widget", price=9.99))
        await repo.save(order)

        stream = await store.read_stream("order-1")
        assert len(stream.events) == 2
        assert isinstance(stream.events[0], OrderPlaced)
        assert isinstance(stream.events[1], LineItemAdded)

    @pytest.mark.anyio
    async def test_save_raises_concurrency_on_version_mismatch(self) -> None:
        """When two repositories share the same store, a save on a stale
        aggregate (loaded before another save happened) raises
        ConcurrencyError."""
        store = FakeEventStore()
        repo1: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store, TestOrder
        )
        repo2: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store, TestOrder
        )

        order = TestOrder(id="order-1")
        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        await repo1.save(order)

        # Load a fresh copy -- simulates a concurrent request
        loaded = await repo2.get_by_id("order-1")
        assert loaded is not None

        # First request adds another event and saves
        order._apply(LineItemAdded(order_id="order-1", item_name="Widget", price=9.99))
        await repo1.save(order)

        # Second request tries to save with stale version
        loaded._apply(LineItemAdded(order_id="order-1", item_name="Gadget", price=5.99))
        with pytest.raises(ConcurrencyError, match="Version mismatch"):
            await repo2.save(loaded)


# ===================================================================
# get_by_id() -- Loading aggregate by replaying event stream
# ===================================================================


class TestGetById:
    """get_by_id() -- loading aggregate by replaying event stream."""

    @pytest.mark.anyio
    async def test_get_by_id_returns_reconstituted_aggregate(self) -> None:
        """A saved aggregate can be loaded back with its state fully
        restored and the correct version."""
        store = FakeEventStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store, TestOrder
        )

        order = TestOrder(id="order-1")
        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        order._apply(LineItemAdded(order_id="order-1", item_name="Widget", price=9.99))
        await repo.save(order)

        loaded = await repo.get_by_id("order-1")
        assert loaded is not None
        assert loaded.id == "order-1"
        assert loaded.version == 2
        assert loaded.customer_name == "Alice"
        assert loaded.status == "placed"
        assert len(loaded.items) == 1
        assert loaded.items[0]["name"] == "Widget"

    @pytest.mark.anyio
    async def test_get_by_id_returns_none_for_missing(self) -> None:
        """get_by_id for an aggregate that was never saved returns None."""
        store = FakeEventStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store, TestOrder
        )

        result = await repo.get_by_id("nonexistent")
        assert result is None

    @pytest.mark.anyio
    async def test_get_by_id_replays_all_events(self) -> None:
        """All events in the stream are replayed, restoring full state and
        version."""
        store = FakeEventStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store, TestOrder
        )

        order = TestOrder(id="order-1")
        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        order._apply(LineItemAdded(order_id="order-1", item_name="Widget", price=9.99))
        order._apply(LineItemAdded(order_id="order-1", item_name="Gadget", price=5.99))
        await repo.save(order)

        loaded = await repo.get_by_id("order-1")
        assert loaded is not None
        assert loaded.version == 3
        assert len(loaded.items) == 2

    @pytest.mark.anyio
    async def test_get_by_id_does_not_buffer_events(self) -> None:
        """Reconstitution via get_by_id uses _replay, so no events are
        buffered in the loaded aggregate."""
        store = FakeEventStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store, TestOrder
        )

        order = TestOrder(id="order-1")
        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        order._apply(LineItemAdded(order_id="order-1", item_name="Widget", price=9.99))
        await repo.save(order)

        loaded = await repo.get_by_id("order-1")
        assert loaded is not None
        assert loaded.pull_events() == []


# ===================================================================
# Round-trip: save -> get -> modify -> save -> get
# ===================================================================


class TestRoundTrip:
    """Full round-trip: save, load, modify, save, and load again."""

    @pytest.mark.anyio
    async def test_save_get_modify_save(self) -> None:
        """A full lifecycle -- create, save, load, apply new event, save,
        reload -- produces the correct final state and version."""
        store = FakeEventStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store, TestOrder
        )

        order = TestOrder(id="order-1")
        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        await repo.save(order)

        loaded = await repo.get_by_id("order-1")
        assert loaded is not None
        assert loaded.customer_name == "Alice"
        assert loaded.status == "placed"
        assert loaded.version == 1

        loaded._apply(LineItemAdded(order_id="order-1", item_name="Widget", price=9.99))
        await repo.save(loaded)

        reloaded = await repo.get_by_id("order-1")
        assert reloaded is not None
        assert reloaded.version == 2
        assert len(reloaded.items) == 1
        assert reloaded.items[0]["name"] == "Widget"
        assert reloaded.items[0]["price"] == 9.99

    @pytest.mark.anyio
    async def test_multiple_aggregates_independent(self) -> None:
        """Different aggregate IDs are independently saved and loaded
        without cross-contamination."""
        store = FakeEventStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store, TestOrder
        )

        order_a = TestOrder(id="order-a")
        order_a._apply(OrderPlaced(order_id="order-a", customer_name="Alice"))
        await repo.save(order_a)

        order_b = TestOrder(id="order-b")
        order_b._apply(OrderPlaced(order_id="order-b", customer_name="Bob"))
        await repo.save(order_b)

        loaded_a = await repo.get_by_id("order-a")
        loaded_b = await repo.get_by_id("order-b")

        assert loaded_a is not None
        assert loaded_b is not None
        assert loaded_a.customer_name == "Alice"
        assert loaded_b.customer_name == "Bob"
        assert loaded_a.id == "order-a"
        assert loaded_b.id == "order-b"


# ===================================================================
# Protocol conformance
# ===================================================================


class TestEventSourcedRepositoryProtocol:
    """FakeEventSourcedRepository satisfies EventSourcedRepository protocol."""

    def test_isinstance_check_passes(self) -> None:
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            FakeEventStore(), TestOrder
        )
        assert isinstance(repo, EventSourcedRepository)

    def test_protocol_methods_are_async(self) -> None:
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            FakeEventStore(), TestOrder
        )
        assert inspect.iscoroutinefunction(repo.save)
        assert inspect.iscoroutinefunction(repo.get_by_id)


# ===================================================================
# get_by_id() with SnapshotStore -- fast hydration from snapshot
# ===================================================================


class TestGetByIdWithSnapshot:
    """get_by_id() with a SnapshotStore -- fast hydration from snapshot."""

    @pytest.mark.anyio
    async def test_snapshot_hit_replays_tail_events(self) -> None:
        """Snapshot at version 2, 3 more events appended.  get_by_id
        replays only the 3 tail events, returning aggregate with version 5
        and correct final state.
        """
        store = FakeEventStore()
        snapshot_store = FakeSnapshotStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store, TestOrder
        )

        order = TestOrder(id="order-1")
        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        order._apply(LineItemAdded(order_id="order-1", item_name="Widget", price=9.99))
        await repo.save(order)

        # Take a snapshot at version 2 manually
        snapshot = order._take_snapshot()
        await snapshot_store.save("TestOrder", snapshot)

        # Append 3 more events
        order._apply(LineItemAdded(order_id="order-1", item_name="Gadget", price=5.99))
        order._apply(
            LineItemAdded(order_id="order-1", item_name="Doohickey", price=2.99)
        )
        order._apply(OrderCancelled(order_id="order-1", reason="no longer needed"))
        await repo.save(order)

        loaded = await repo.get_by_id("order-1", snapshot_store=snapshot_store)
        assert loaded is not None
        assert loaded.version == 5
        assert loaded.customer_name == "Alice"
        assert loaded.status == "cancelled"
        assert len(loaded.items) == 3

    @pytest.mark.anyio
    async def test_snapshot_at_head_replays_zero_events(self) -> None:
        """Snapshot at version 5 with exactly 5 total events.  get_by_id
        replays 0 tail events and returns aggregate from snapshot state.
        """
        store = FakeEventStore()
        snapshot_store = FakeSnapshotStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store, TestOrder
        )

        order = TestOrder(id="order-1")
        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        order._apply(LineItemAdded(order_id="order-1", item_name="Widget", price=9.99))
        order._apply(LineItemAdded(order_id="order-1", item_name="Gadget", price=5.99))
        order._apply(
            LineItemAdded(order_id="order-1", item_name="Doohickey", price=2.99)
        )
        order._apply(OrderCancelled(order_id="order-1", reason="no longer needed"))
        await repo.save(order)

        # Snapshot at version 5 -- same as total event count
        snapshot = order._take_snapshot()
        await snapshot_store.save("TestOrder", snapshot)

        loaded = await repo.get_by_id("order-1", snapshot_store=snapshot_store)
        assert loaded is not None
        assert loaded.version == 5
        assert loaded.customer_name == "Alice"
        assert loaded.status == "cancelled"
        assert len(loaded.items) == 3

    @pytest.mark.anyio
    async def test_falls_back_when_no_snapshot_in_store(self) -> None:
        """snapshot_store argument provided but no snapshot exists for
        the aggregate.  Falls back to full event replay.
        """
        store = FakeEventStore()
        snapshot_store = FakeSnapshotStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store, TestOrder
        )

        order = TestOrder(id="order-1")
        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        order._apply(LineItemAdded(order_id="order-1", item_name="Widget", price=9.99))
        await repo.save(order)

        # snapshot_store is empty -- no snapshot ever saved
        loaded = await repo.get_by_id("order-1", snapshot_store=snapshot_store)
        assert loaded is not None
        assert loaded.version == 2
        assert loaded.customer_name == "Alice"
        assert len(loaded.items) == 1

    @pytest.mark.anyio
    async def test_falls_back_when_no_snapshot_store_arg(self) -> None:
        """get_by_id() without snapshot_store argument defaults to full
        event replay.  Backward compatible with non-snapshot usage.
        """
        store = FakeEventStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store, TestOrder
        )

        order = TestOrder(id="order-1")
        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        order._apply(LineItemAdded(order_id="order-1", item_name="Widget", price=9.99))
        await repo.save(order)

        loaded = await repo.get_by_id("order-1")
        assert loaded is not None
        assert loaded.version == 2

    @pytest.mark.anyio
    async def test_different_aggregate_id_falls_back(self) -> None:
        """Snapshot exists for one aggregate ID but loading a different
        aggregate ID falls back to full replay.
        """
        store = FakeEventStore()
        snapshot_store = FakeSnapshotStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store, TestOrder
        )

        order_1 = TestOrder(id="order-1")
        order_1._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        await repo.save(order_1)

        order_2 = TestOrder(id="order-2")
        order_2._apply(OrderPlaced(order_id="order-2", customer_name="Bob"))
        await repo.save(order_2)

        # Snapshot for order-1 only
        snapshot = order_1._take_snapshot()
        await snapshot_store.save("TestOrder", snapshot)

        # Loading order-2 should fall back to full replay
        loaded = await repo.get_by_id("order-2", snapshot_store=snapshot_store)
        assert loaded is not None
        assert loaded.version == 1
        assert loaded.customer_name == "Bob"

    @pytest.mark.anyio
    async def test_snapshot_type_isolation(self) -> None:
        """Snapshot for a different aggregate type is ignored.
        FakeSnapshotStore keys by (aggregate_type, aggregate_id), so a
        snapshot saved under a different type is not found.
        """
        store = FakeEventStore()
        snapshot_store = FakeSnapshotStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store, TestOrder
        )

        order = TestOrder(id="order-1")
        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        order._apply(LineItemAdded(order_id="order-1", item_name="Widget", price=9.99))
        await repo.save(order)

        # Save snapshot under a different aggregate type
        snapshot = order._take_snapshot()
        await snapshot_store.save("OtherOrder", snapshot)

        # Loading TestOrder/order-1 should NOT find the snapshot
        loaded = await repo.get_by_id("order-1", snapshot_store=snapshot_store)
        assert loaded is not None
        assert loaded.version == 2  # full replay
        assert loaded.customer_name == "Alice"

    @pytest.mark.anyio
    async def test_snapshot_returned_when_stream_deleted(self) -> None:
        """Snapshot exists but the event stream has been removed.
        get_by_id returns the aggregate from snapshot state, not None.
        """
        store = FakeEventStore()
        snapshot_store = FakeSnapshotStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store, TestOrder
        )

        order = TestOrder(id="order-1")
        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        order._apply(LineItemAdded(order_id="order-1", item_name="Widget", price=9.99))
        await repo.save(order)

        # Take snapshot at version 2
        snapshot = order._take_snapshot()
        await snapshot_store.save("TestOrder", snapshot)

        # Remove the stream from the event store
        store._store.pop("order-1", None)

        # Loading should return the aggregate from snapshot, not None
        loaded = await repo.get_by_id("order-1", snapshot_store=snapshot_store)
        assert loaded is not None
        assert loaded.version == 2
        assert loaded.customer_name == "Alice"
        assert len(loaded.items) == 1


# ===================================================================
# save() with SnapshotStore -- write-path snapshot integration
# ===================================================================


class TestSaveWithSnapshot:
    """save() with a SnapshotStore and SnapshotPolicy."""

    @pytest.mark.anyio
    async def test_trigger_on_threshold_hit(self) -> None:
        """Snapshot threshold=5, apply 5 events, save.  Snapshot is
        stored with version=5.
        """
        snapshot_store = FakeSnapshotStore()
        policy = SnapshotThresholdPolicy(threshold=5)
        store = FakeEventStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store,
            TestOrder,
            snapshot_store=snapshot_store,
            snapshot_policy=policy,
        )

        order = TestOrder(id="order-1")
        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        order._apply(LineItemAdded(order_id="order-1", item_name="A", price=1.0))
        order._apply(LineItemAdded(order_id="order-1", item_name="B", price=2.0))
        order._apply(LineItemAdded(order_id="order-1", item_name="C", price=3.0))
        order._apply(LineItemAdded(order_id="order-1", item_name="D", price=4.0))
        await repo.save(order)

        snapshot = await snapshot_store.get("TestOrder", "order-1")
        assert snapshot is not None
        assert snapshot.version == 5

    @pytest.mark.anyio
    async def test_no_trigger_below_threshold(self) -> None:
        """Snapshot threshold=5, apply 3 events, save.  No snapshot is
        stored.  Events are still persisted to the event store.
        """
        snapshot_store = FakeSnapshotStore()
        policy = SnapshotThresholdPolicy(threshold=5)
        store = FakeEventStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store,
            TestOrder,
            snapshot_store=snapshot_store,
            snapshot_policy=policy,
        )

        order = TestOrder(id="order-1")
        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        order._apply(LineItemAdded(order_id="order-1", item_name="A", price=1.0))
        order._apply(LineItemAdded(order_id="order-1", item_name="B", price=2.0))
        await repo.save(order)

        snapshot = await snapshot_store.get("TestOrder", "order-1")
        assert snapshot is None

        # Events were still persisted
        stream = await store.read_stream("order-1")
        assert len(stream.events) == 3

    @pytest.mark.anyio
    async def test_no_snapshot_store_configured(self) -> None:
        """save() with no snapshot_store configured.  No error raised,
        events are still persisted.
        """
        store = FakeEventStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store, TestOrder
        )

        order = TestOrder(id="order-1")
        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        await repo.save(order)  # should not raise

        stream = await store.read_stream("order-1")
        assert len(stream.events) == 1

    @pytest.mark.anyio
    async def test_no_snapshot_policy_configured(self) -> None:
        """snapshot_store is set but no snapshot_policy.  No snapshot
        is taken despite events being saved.
        """
        snapshot_store = FakeSnapshotStore()
        store = FakeEventStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store,
            TestOrder,
            snapshot_store=snapshot_store,
        )

        order = TestOrder(id="order-1")
        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        order._apply(LineItemAdded(order_id="order-1", item_name="Widget", price=9.99))
        await repo.save(order)

        snapshot = await snapshot_store.get("TestOrder", "order-1")
        assert snapshot is None

    @pytest.mark.anyio
    async def test_per_call_override_triggers_snapshot(self) -> None:
        """Repository constructed without snapshot config.  Per-call
        save(snapshot_store=..., snapshot_policy=...) triggers snapshot
        correctly.
        """
        store = FakeEventStore()
        snapshot_store = FakeSnapshotStore()
        policy = SnapshotThresholdPolicy(threshold=1)
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store,
            TestOrder,
        )

        order = TestOrder(id="order-1")
        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        await repo.save(
            order,
            snapshot_store=snapshot_store,
            snapshot_policy=policy,
        )

        snapshot = await snapshot_store.get("TestOrder", "order-1")
        assert snapshot is not None
        assert snapshot.version == 1

    @pytest.mark.anyio
    async def test_snapshot_state_matches_aggregate(self) -> None:
        """After save with snapshot trigger, the snapshot's state dict
        matches the aggregate's model_dump() minus the version field.
        """
        snapshot_store = FakeSnapshotStore()
        policy = SnapshotThresholdPolicy(threshold=1)
        store = FakeEventStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store,
            TestOrder,
            snapshot_store=snapshot_store,
            snapshot_policy=policy,
        )

        order = TestOrder(id="order-1")
        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        order._apply(LineItemAdded(order_id="order-1", item_name="Widget", price=9.99))
        await repo.save(order)

        snapshot = await snapshot_store.get("TestOrder", "order-1")
        assert snapshot is not None

        expected = order.model_dump(mode="python")
        expected.pop("version", None)
        assert snapshot.state == expected

    @pytest.mark.anyio
    async def test_threshold_zero_snapshots_on_every_save(self) -> None:
        """SnapshotThresholdPolicy(threshold=0) triggers a snapshot on
        every save that has pending events.
        """
        snapshot_store = FakeSnapshotStore()
        policy = SnapshotThresholdPolicy(threshold=0)
        store = FakeEventStore()
        repo: FakeEventSourcedRepository[TestOrder, str] = FakeEventSourcedRepository(
            store,
            TestOrder,
            snapshot_store=snapshot_store,
            snapshot_policy=policy,
        )

        order = TestOrder(id="order-1")
        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        await repo.save(order)

        snap1 = await snapshot_store.get("TestOrder", "order-1")
        assert snap1 is not None
        assert snap1.version == 1

        order._apply(LineItemAdded(order_id="order-1", item_name="A", price=1.0))
        await repo.save(order)

        snap2 = await snapshot_store.get("TestOrder", "order-1")
        assert snap2 is not None
        assert snap2.version == 2
