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
from pydomain.testing.fake_event_sourced_repository import FakeEventSourcedRepository
from pydomain.testing.fake_event_store import FakeEventStore
from tests.es.conftest import LineItemAdded, OrderPlaced, TestOrder

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
