"""Tests for InMemoryEventStore -- the in-memory fake for the EventStore protocol.

Covers appending, reading, serialization round-trips, stream isolation,
version-based ordering, and the runtime-checkable Protocol conformance.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from pydomain.cqrs import EventStore
from pydomain.ddd import DomainEvent
from pydomain.ddd.exceptions import ConcurrencyError
from pydomain.infrastructure import EventRegistry
from pydomain.testing import InMemoryEventStore

# ---------------------------------------------------------------------------
# Module-level DomainEvent subclasses for testing
# ---------------------------------------------------------------------------


class ItemAddedToCart(DomainEvent):
    """A test domain event representing adding an item to a cart."""

    item_id: str
    quantity: int


class CartCleared(DomainEvent):
    """A test domain event representing clearing a cart."""

    reason: str = ""


# ===================================================================
# Appending Events
# ===================================================================


class TestAppendEvents:
    """``append_events()`` -- adding events to a stream."""

    @pytest.mark.anyio
    async def test_append_to_new_stream_with_expected_version_zero(self) -> None:
        """Appending events to a new stream with ``expected_version=0``
        succeeds, and the events are readable afterwards."""
        registry = EventRegistry()
        registry.register(ItemAddedToCart)
        store = InMemoryEventStore(registry)
        stream_id = uuid4()

        event = ItemAddedToCart(item_id="sku-001", quantity=2)
        await store.append_events(stream_id, [event], expected_version=0)

        events = await store.read_events(stream_id)
        assert len(events) == 1
        assert isinstance(events[0], ItemAddedToCart)
        assert events[0].item_id == "sku-001"
        assert events[0].quantity == 2

    @pytest.mark.anyio
    async def test_append_to_existing_stream_with_correct_version(self) -> None:
        """Appending events to an existing stream with a matching
        ``expected_version`` succeeds and the new events appear after the
        existing ones."""
        registry = EventRegistry()
        registry.register(ItemAddedToCart)
        store = InMemoryEventStore(registry)
        stream_id = uuid4()

        event_a = ItemAddedToCart(item_id="sku-001", quantity=2)
        await store.append_events(stream_id, [event_a], expected_version=0)

        event_b = ItemAddedToCart(item_id="sku-002", quantity=5)
        await store.append_events(stream_id, [event_b], expected_version=1)

        events = await store.read_events(stream_id)
        assert len(events) == 2
        assert isinstance(events[0], ItemAddedToCart)
        assert events[0].item_id == "sku-001"
        assert isinstance(events[1], ItemAddedToCart)
        assert events[1].item_id == "sku-002"

    @pytest.mark.anyio
    async def test_append_with_wrong_expected_version_raises_concurrency_error(
        self,
    ) -> None:
        """Appending with an ``expected_version`` that does not match the
        current stream length raises ``ConcurrencyError``."""
        registry = EventRegistry()
        registry.register(ItemAddedToCart)
        store = InMemoryEventStore(registry)
        stream_id = uuid4()

        await store.append_events(
            stream_id,
            [ItemAddedToCart(item_id="sku-001", quantity=2)],
            expected_version=0,
        )

        with pytest.raises(ConcurrencyError, match="Version mismatch"):
            await store.append_events(
                stream_id,
                [ItemAddedToCart(item_id="sku-002", quantity=5)],
                expected_version=0,  # should be 1
            )

    @pytest.mark.anyio
    async def test_append_multiple_events_in_one_call(self) -> None:
        """Appending several events in a single call stores all of them."""
        registry = EventRegistry()
        registry.register(ItemAddedToCart)
        store = InMemoryEventStore(registry)
        stream_id = uuid4()

        batch = [
            ItemAddedToCart(item_id="sku-001", quantity=2),
            ItemAddedToCart(item_id="sku-002", quantity=5),
        ]
        await store.append_events(stream_id, batch, expected_version=0)

        events = await store.read_events(stream_id)
        assert len(events) == 2
        assert isinstance(events[0], ItemAddedToCart)
        assert events[0].item_id == "sku-001"
        assert isinstance(events[1], ItemAddedToCart)
        assert events[1].item_id == "sku-002"

    @pytest.mark.anyio
    async def test_append_after_concurrency_retry_succeeds(self) -> None:
        """After a concurrency error, using the corrected expected version
        succeeds."""
        registry = EventRegistry()
        registry.register(ItemAddedToCart)
        store = InMemoryEventStore(registry)
        stream_id = uuid4()

        await store.append_events(
            stream_id,
            [ItemAddedToCart(item_id="sku-001", quantity=2)],
            expected_version=0,
        )

        with pytest.raises(ConcurrencyError):
            await store.append_events(
                stream_id,
                [ItemAddedToCart(item_id="sku-002", quantity=5)],
                expected_version=0,
            )

        # Retry with the correct version
        await store.append_events(
            stream_id,
            [ItemAddedToCart(item_id="sku-002", quantity=5)],
            expected_version=1,
        )
        events = await store.read_events(stream_id)
        assert len(events) == 2


# ===================================================================
# Reading Events
# ===================================================================


class TestReadEvents:
    """``read_events()`` -- retrieving events from a stream."""

    @pytest.mark.anyio
    async def test_read_unknown_stream_returns_empty_list(self) -> None:
        """Reading from a stream identity that has never been written to
        returns an empty list."""
        registry = EventRegistry()
        store = InMemoryEventStore(registry)
        unknown_id = uuid4()

        events = await store.read_events(unknown_id)
        assert events == []

    @pytest.mark.anyio
    async def test_read_returns_events_in_version_order(self) -> None:
        """``read_events`` returns events in the order they were appended,
        which corresponds to ascending version numbers."""
        registry = EventRegistry()
        registry.register(ItemAddedToCart)
        store = InMemoryEventStore(registry)
        stream_id = uuid4()

        await store.append_events(
            stream_id,
            [ItemAddedToCart(item_id="first", quantity=1)],
            expected_version=0,
        )
        await store.append_events(
            stream_id,
            [ItemAddedToCart(item_id="second", quantity=2)],
            expected_version=1,
        )
        await store.append_events(
            stream_id,
            [ItemAddedToCart(item_id="third", quantity=3)],
            expected_version=2,
        )

        events = await store.read_events(stream_id)
        assert len(events) == 3
        assert isinstance(events[0], ItemAddedToCart)
        assert events[0].item_id == "first"
        assert isinstance(events[1], ItemAddedToCart)
        assert events[1].item_id == "second"
        assert isinstance(events[2], ItemAddedToCart)
        assert events[2].item_id == "third"

    @pytest.mark.anyio
    async def test_read_returns_copies_not_references(self) -> None:
        """The deserialized events are independent objects, not references to
        internal store state."""
        registry = EventRegistry()
        registry.register(ItemAddedToCart)
        store = InMemoryEventStore(registry)
        stream_id = uuid4()

        await store.append_events(
            stream_id,
            [ItemAddedToCart(item_id="sku-001", quantity=2)],
            expected_version=0,
        )

        events_a = await store.read_events(stream_id)
        events_b = await store.read_events(stream_id)

        # Same logical content but different object identities
        assert events_a[0] == events_b[0]
        assert events_a[0] is not events_b[0]


# ===================================================================
# Serialization Round-Trip
# ===================================================================


class TestSerializationRoundTrip:
    """Verify that events survive a serialize-deserialize round trip through
    the ``EventRegistry``."""

    @pytest.mark.anyio
    async def test_round_trip_preserves_event_type(self) -> None:
        """After a round trip the deserialized event is the same type as the
        original."""
        registry = EventRegistry()
        registry.register(ItemAddedToCart)
        store = InMemoryEventStore(registry)
        stream_id = uuid4()

        original = ItemAddedToCart(item_id="sku-001", quantity=3)
        await store.append_events(stream_id, [original], expected_version=0)

        [restored] = await store.read_events(stream_id)
        assert isinstance(restored, ItemAddedToCart)

    @pytest.mark.anyio
    async def test_round_trip_preserves_field_values(self) -> None:
        """Custom field values survive the serialize/deserialize cycle."""
        registry = EventRegistry()
        registry.register(ItemAddedToCart)
        store = InMemoryEventStore(registry)
        stream_id = uuid4()

        original = ItemAddedToCart(item_id="sku-001", quantity=3)
        await store.append_events(stream_id, [original], expected_version=0)

        [restored] = await store.read_events(stream_id)
        assert isinstance(restored, ItemAddedToCart)
        assert restored.item_id == original.item_id
        assert restored.quantity == original.quantity

    @pytest.mark.anyio
    async def test_round_trip_preserves_event_id_and_timestamps(self) -> None:
        """Auto-generated fields (``event_id``, ``occurred_at``,
        ``correlation_id``, ``causation_id``) are preserved through
        serialization."""
        registry = EventRegistry()
        registry.register(ItemAddedToCart)
        store = InMemoryEventStore(registry)
        stream_id = uuid4()

        original = ItemAddedToCart(item_id="sku-001", quantity=3)
        await store.append_events(stream_id, [original], expected_version=0)

        [restored] = await store.read_events(stream_id)
        assert restored.event_id == original.event_id
        assert restored.occurred_at == original.occurred_at
        assert restored.correlation_id == original.correlation_id
        assert restored.causation_id == original.causation_id

    @pytest.mark.anyio
    async def test_round_trip_with_multiple_event_types(self) -> None:
        """Multiple registered event types survive a round trip and are
        restored to their correct types."""
        registry = EventRegistry()
        registry.register(ItemAddedToCart)
        registry.register(CartCleared)
        store = InMemoryEventStore(registry)
        stream_id = uuid4()

        await store.append_events(
            stream_id,
            [ItemAddedToCart(item_id="sku-001", quantity=1)],
            expected_version=0,
        )
        await store.append_events(
            stream_id,
            [CartCleared(reason="checkout")],
            expected_version=1,
        )

        events = await store.read_events(stream_id)
        assert len(events) == 2
        assert isinstance(events[0], ItemAddedToCart)
        assert isinstance(events[1], CartCleared)
        assert events[1].reason == "checkout"

    @pytest.mark.anyio
    async def test_unregistered_event_type_falls_back_to_generic(self) -> None:
        """When the event type is not registered in the ``EventRegistry``,
        deserialization returns a ``GenericDomainEvent`` carrying the raw
        data."""
        registry = EventRegistry()
        # Intentionally *not* registering ItemAddedToCart
        store = InMemoryEventStore(registry)
        stream_id = uuid4()

        original = ItemAddedToCart(item_id="sku-001", quantity=3)
        await store.append_events(stream_id, [original], expected_version=0)

        [restored] = await store.read_events(stream_id)
        from pydomain.infrastructure.event_registry import GenericDomainEvent

        assert isinstance(restored, GenericDomainEvent)
        assert restored.type == "ItemAddedToCart"
        assert restored.data["item_id"] == "sku-001"
        assert restored.data["quantity"] == 3


# ===================================================================
# Stream Isolation
# ===================================================================


class TestStreamIsolation:
    """Events for different streams must not interfere with each other."""

    @pytest.mark.anyio
    async def test_events_for_different_streams_dont_interfere(self) -> None:
        """Events written to one stream do not appear when reading a
        different stream."""
        registry = EventRegistry()
        registry.register(ItemAddedToCart)
        store = InMemoryEventStore(registry)

        stream_a = uuid4()
        stream_b = uuid4()

        await store.append_events(
            stream_a,
            [ItemAddedToCart(item_id="sku-a1", quantity=1)],
            expected_version=0,
        )
        await store.append_events(
            stream_b,
            [ItemAddedToCart(item_id="sku-b1", quantity=2)],
            expected_version=0,
        )

        events_a = await store.read_events(stream_a)
        events_b = await store.read_events(stream_b)

        assert len(events_a) == 1
        assert len(events_b) == 1
        assert isinstance(events_a[0], ItemAddedToCart)
        assert events_a[0].item_id == "sku-a1"
        assert isinstance(events_b[0], ItemAddedToCart)
        assert events_b[0].item_id == "sku-b1"

    @pytest.mark.anyio
    async def test_version_tracking_is_per_stream(self) -> None:
        """Each stream maintains its own version counter independently."""
        registry = EventRegistry()
        registry.register(ItemAddedToCart)
        store = InMemoryEventStore(registry)

        stream_a = uuid4()
        stream_b = uuid4()

        await store.append_events(
            stream_a,
            [ItemAddedToCart(item_id="sku-a1", quantity=1)],
            expected_version=0,
        )
        await store.append_events(
            stream_b,
            [ItemAddedToCart(item_id="sku-b1", quantity=2)],
            expected_version=0,
        )

        # Appending to stream_b uses its own expected_version
        await store.append_events(
            stream_b,
            [ItemAddedToCart(item_id="sku-b2", quantity=3)],
            expected_version=1,
        )

        events_a = await store.read_events(stream_a)
        events_b = await store.read_events(stream_b)

        assert len(events_a) == 1
        assert len(events_b) == 2

    @pytest.mark.anyio
    async def test_empty_stream_does_not_affect_others(self) -> None:
        """Reading from an untouched stream returns an empty list even when
        other streams have events."""
        registry = EventRegistry()
        store = InMemoryEventStore(registry)
        stream_a = uuid4()
        stream_b = uuid4()

        await store.append_events(
            stream_a,
            [ItemAddedToCart(item_id="sku-001", quantity=1)],
            expected_version=0,
        )

        assert await store.read_events(stream_b) == []


# ===================================================================
# EventStore Protocol Conformance
# ===================================================================


class TestEventStoreProtocol:
    """``InMemoryEventStore`` must satisfy the ``EventStore`` protocol."""

    @pytest.mark.anyio
    async def test_isinstance_check_passes(self) -> None:
        """``isinstance(InMemoryEventStore(...), EventStore)`` returns
        ``True``."""
        registry = EventRegistry()
        store = InMemoryEventStore(registry)
        assert isinstance(store, EventStore)

    def test_protocol_methods_are_async(self) -> None:
        """Both protocol methods are ``async def`` (confirmed by calling
        them and inspecting the return type)."""
        registry = EventRegistry()
        store = InMemoryEventStore(registry)
        stream_id = uuid4()
        event = ItemAddedToCart(item_id="x", quantity=1)

        append_coro = store.append_events(stream_id, [event], expected_version=0)
        read_coro = store.read_events(stream_id)

        import inspect

        assert inspect.iscoroutine(append_coro)
        assert inspect.iscoroutine(read_coro)
