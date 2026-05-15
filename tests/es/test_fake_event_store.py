# mypy: disable-error-code="attr-defined"
"""Tests for FakeEventStore -- the in-memory fake for the ES EventStore protocol.

Covers appending, reading, stream lifecycle exceptions (StreamNotFoundError,
StreamAlreadyExistsError, ConcurrencyError), version tracking, stream
isolation, and runtime-checkable Protocol conformance.

FakeEventStore does NOT use EventRegistry -- it stores raw DomainEvent
objects directly.  These tests do not depend on serialization.
"""

from __future__ import annotations

import inspect
from uuid import uuid4

import pytest

from pydomain.ddd import DomainEvent
from pydomain.ddd.exceptions import ConcurrencyError, DomainError
from pydomain.es.event_store import EventStore
from pydomain.es.event_stream import EventStream
from pydomain.es.exceptions import (
    DuplicateCommandError,
    StreamAlreadyExistsError,
    StreamNotFoundError,
)
from pydomain.testing.fake_event_store import FakeEventStore

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
# Appending to a Stream
# ===================================================================


class TestAppendToStream:
    """``append_to_stream()`` -- adding events to a stream."""

    @pytest.mark.anyio
    async def test_append_to_new_stream_with_expected_version_zero(
        self,
    ) -> None:
        """Appending events to a new stream with ``expected_version=0``
        succeeds, and the events are readable afterwards."""
        store = FakeEventStore()

        event = ItemAddedToCart(item_id="sku-001", quantity=2)
        await store.append_to_stream("cart-001", [event], expected_version=0)

        stream = await store.read_stream("cart-001")
        assert isinstance(stream, EventStream)
        assert stream.version == 1
        assert len(stream.events) == 1
        assert stream.events[0].item_id == "sku-001"
        assert stream.events[0].quantity == 2

    @pytest.mark.anyio
    async def test_append_raises_stream_already_exists_error(self) -> None:
        """Appending with ``expected_version=0`` when the stream already
        exists raises ``StreamAlreadyExistsError`` with the aggregate_id."""
        store = FakeEventStore()
        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="sku-001", quantity=2)],
            expected_version=0,
        )

        with pytest.raises(StreamAlreadyExistsError, match="already exists") as exc:
            await store.append_to_stream(
                "cart-001",
                [ItemAddedToCart(item_id="sku-002", quantity=5)],
                expected_version=0,
            )
        assert exc.value.aggregate_id == "cart-001"

    @pytest.mark.anyio
    async def test_append_to_existing_stream_with_correct_version(
        self,
    ) -> None:
        """Appending events to an existing stream with a matching
        ``expected_version`` succeeds, and the new events appear after the
        existing ones."""
        store = FakeEventStore()

        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="sku-001", quantity=2)],
            expected_version=0,
        )
        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="sku-002", quantity=5)],
            expected_version=1,
        )

        stream = await store.read_stream("cart-001")
        assert len(stream.events) == 2
        assert stream.events[0].item_id == "sku-001"
        assert stream.events[1].item_id == "sku-002"

    @pytest.mark.anyio
    async def test_append_raises_concurrency_error_on_version_mismatch(
        self,
    ) -> None:
        """Appending with an ``expected_version`` that does not match the
        current stream length raises ``ConcurrencyError`` with a descriptive
        message that includes expected and actual versions.

        Note: ``expected_version=0`` on an existing stream is caught by
        ``StreamAlreadyExistsError`` first, so we use a non-zero wrong
        expected version to trigger the concurrency check.
        """
        store = FakeEventStore()

        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="sku-001", quantity=2)],
            expected_version=0,
        )

        with pytest.raises(ConcurrencyError, match="Version mismatch") as exc:
            await store.append_to_stream(
                "cart-001",
                [ItemAddedToCart(item_id="sku-002", quantity=5)],
                expected_version=2,  # should be 1
            )
        message = str(exc.value)
        assert "cart-001" in message
        assert "expected 2, found 1" in message

    @pytest.mark.anyio
    async def test_append_batch_events(self) -> None:
        """Appending multiple events in a single call stores all of them."""
        store = FakeEventStore()

        batch = [
            ItemAddedToCart(item_id="sku-001", quantity=2),
            ItemAddedToCart(item_id="sku-002", quantity=5),
        ]
        await store.append_to_stream("cart-001", batch, expected_version=0)

        stream = await store.read_stream("cart-001")
        assert len(stream.events) == 2
        assert stream.events[0].item_id == "sku-001"
        assert stream.events[1].item_id == "sku-002"
        assert stream.version == 2


# ===================================================================
# Reading from a Stream
# ===================================================================


class TestReadStream:
    """``read_stream()`` -- retrieving events from a stream."""

    @pytest.mark.anyio
    async def test_read_returns_events_in_append_order(self) -> None:
        """``read_stream`` returns events in the same order they were
        appended."""
        store = FakeEventStore()

        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="first", quantity=1)],
            expected_version=0,
        )
        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="second", quantity=2)],
            expected_version=1,
        )
        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="third", quantity=3)],
            expected_version=2,
        )

        stream = await store.read_stream("cart-001")
        assert len(stream.events) == 3
        assert stream.events[0].item_id == "first"
        assert stream.events[1].item_id == "second"
        assert stream.events[2].item_id == "third"

    @pytest.mark.anyio
    async def test_read_unknown_stream_raises_stream_not_found(self) -> None:
        """Reading from a stream identity that has never been appended to
        raises ``StreamNotFoundError`` with the aggregate_id in the
        message."""
        store = FakeEventStore()

        with pytest.raises(StreamNotFoundError, match="not found") as exc:
            await store.read_stream("non-existent-cart")
        assert exc.value.aggregate_id == "non-existent-cart"

    @pytest.mark.anyio
    async def test_read_with_from_version_returns_slice(self) -> None:
        """``read_stream`` with ``from_version`` returns only events from
        that version onward, while the stream version reflects the total
        event count."""
        store = FakeEventStore()

        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="first", quantity=1)],
            expected_version=0,
        )
        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="second", quantity=2)],
            expected_version=1,
        )
        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="third", quantity=3)],
            expected_version=2,
        )

        # Read from version 1 (0-indexed) -- skip "first"
        stream = await store.read_stream("cart-001", from_version=1)
        assert len(stream.events) == 2
        assert stream.events[0].item_id == "second"
        assert stream.events[1].item_id == "third"
        # version is the full stream length, not the slice length
        assert stream.version == 3

    @pytest.mark.anyio
    async def test_read_with_from_version_zero_returns_all_events(
        self,
    ) -> None:
        """``read_stream`` with ``from_version=0`` on an existing stream
        returns all events."""
        store = FakeEventStore()

        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="first", quantity=1)],
            expected_version=0,
        )
        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="second", quantity=2)],
            expected_version=1,
        )

        stream = await store.read_stream("cart-001", from_version=0)
        assert len(stream.events) == 2
        assert stream.events[0].item_id == "first"
        assert stream.events[1].item_id == "second"

    @pytest.mark.anyio
    async def test_read_empty_stream_returns_empty_event_stream(
        self,
    ) -> None:
        """After creating a stream by appending an empty list,
        ``read_stream`` returns ``EventStream(events=[], version=0)``."""
        store = FakeEventStore()

        await store.append_to_stream("cart-001", [], expected_version=0)

        stream = await store.read_stream("cart-001")
        assert stream == EventStream(events=[], version=0)
        assert stream.events == []
        assert stream.version == 0


# ===================================================================
# Version Tracking
# ===================================================================


class TestVersionTracking:
    """Version is tracked per-stream and reflects the number of events."""

    @pytest.mark.anyio
    async def test_version_increments_with_each_append(self) -> None:
        """``EventStream.version`` reflects the total number of events
        appended to the stream."""
        store = FakeEventStore()

        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="sku-001", quantity=1)],
            expected_version=0,
        )
        assert (await store.read_stream("cart-001")).version == 1

        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="sku-002", quantity=2)],
            expected_version=1,
        )
        assert (await store.read_stream("cart-001")).version == 2

        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="sku-003", quantity=3)],
            expected_version=2,
        )
        assert (await store.read_stream("cart-001")).version == 3

    @pytest.mark.anyio
    async def test_version_independent_per_stream(self) -> None:
        """Different streams maintain independent version counters."""
        store = FakeEventStore()

        await store.append_to_stream(
            "cart-a",
            [ItemAddedToCart(item_id="a1", quantity=1)],
            expected_version=0,
        )
        await store.append_to_stream(
            "cart-a",
            [ItemAddedToCart(item_id="a2", quantity=2)],
            expected_version=1,
        )
        await store.append_to_stream(
            "cart-b",
            [ItemAddedToCart(item_id="b1", quantity=1)],
            expected_version=0,
        )

        stream_a = await store.read_stream("cart-a")
        stream_b = await store.read_stream("cart-b")

        assert stream_a.version == 2
        assert stream_b.version == 1


# ===================================================================
# Stream Isolation
# ===================================================================


class TestStreamIsolation:
    """Events for different aggregate IDs must not interfere."""

    @pytest.mark.anyio
    async def test_different_streams_are_independent(self) -> None:
        """Events appended to one stream do not appear when reading a
        different stream."""
        store = FakeEventStore()

        await store.append_to_stream(
            "cart-a",
            [ItemAddedToCart(item_id="a1", quantity=1)],
            expected_version=0,
        )
        await store.append_to_stream(
            "cart-b",
            [ItemAddedToCart(item_id="b1", quantity=2)],
            expected_version=0,
        )

        stream_a = await store.read_stream("cart-a")
        stream_b = await store.read_stream("cart-b")

        assert len(stream_a.events) == 1
        assert len(stream_b.events) == 1
        assert stream_a.events[0].item_id == "a1"
        assert stream_b.events[0].item_id == "b1"

    @pytest.mark.anyio
    async def test_different_event_types_across_streams(self) -> None:
        """Different streams can hold events of different types without
        interference."""
        store = FakeEventStore()

        await store.append_to_stream(
            "cart-a",
            [ItemAddedToCart(item_id="a1", quantity=1)],
            expected_version=0,
        )
        await store.append_to_stream(
            "cart-b",
            [CartCleared(reason="checkout")],
            expected_version=0,
        )

        stream_a = await store.read_stream("cart-a")
        stream_b = await store.read_stream("cart-b")

        assert isinstance(stream_a.events[0], ItemAddedToCart)
        assert isinstance(stream_b.events[0], CartCleared)
        assert stream_b.events[0].reason == "checkout"


# ===================================================================
# Global Log -- read_all
# ===================================================================


class TestReadAll:
    """``read_all()`` -- reading events from the global event log across all
    streams."""

    @pytest.mark.anyio
    async def test_read_all_returns_all_events_in_append_order(
        self,
    ) -> None:
        """Events from multiple streams appear in append (global) order,
        not grouped by stream."""
        store = FakeEventStore()

        await store.append_to_stream(
            "cart-a",
            [ItemAddedToCart(item_id="a1", quantity=1)],
            expected_version=0,
        )
        await store.append_to_stream(
            "cart-b",
            [CartCleared(reason="checkout")],
            expected_version=0,
        )
        await store.append_to_stream(
            "cart-a",
            [ItemAddedToCart(item_id="a2", quantity=2)],
            expected_version=1,
        )

        result = await store.read_all()

        assert result.version == 3
        assert len(result.events) == 3
        # Append order: a1, CartCleared, a2
        assert result.events[0].item_id == "a1"
        assert isinstance(result.events[1], CartCleared)
        assert result.events[1].reason == "checkout"
        assert result.events[2].item_id == "a2"

    @pytest.mark.anyio
    async def test_read_all_respects_from_version(self) -> None:
        """``from_version`` slices the global log, while version still
        reflects total global event count."""
        store = FakeEventStore()

        await store.append_to_stream(
            "cart-a",
            [ItemAddedToCart(item_id="first", quantity=1)],
            expected_version=0,
        )
        await store.append_to_stream(
            "cart-b",
            [ItemAddedToCart(item_id="second", quantity=2)],
            expected_version=0,
        )
        await store.append_to_stream(
            "cart-a",
            [ItemAddedToCart(item_id="third", quantity=3)],
            expected_version=1,
        )

        # Skip the first event (from_version=1)
        result = await store.read_all(from_version=1)

        assert result.version == 3
        assert len(result.events) == 2
        assert result.events[0].item_id == "second"
        assert result.events[1].item_id == "third"

    @pytest.mark.anyio
    async def test_read_all_version_is_global_count(self) -> None:
        """``read_all().version`` equals the total number of events across
        all streams, not the length of the returned slice."""
        store = FakeEventStore()

        # Empty store
        result = await store.read_all()
        assert result.version == 0

        await store.append_to_stream(
            "cart-a",
            [ItemAddedToCart(item_id="a1", quantity=1)],
            expected_version=0,
        )
        result = await store.read_all()
        assert result.version == 1

        await store.append_to_stream(
            "cart-b",
            [ItemAddedToCart(item_id="b1", quantity=1)],
            expected_version=0,
        )
        result = await store.read_all()
        assert result.version == 2

        # Slice should not affect version
        result = await store.read_all(from_version=1)
        assert result.version == 2

    @pytest.mark.anyio
    async def test_read_all_with_empty_store(self) -> None:
        """Reading from a store with no events returns an empty event list
        and version 0."""
        store = FakeEventStore()

        result = await store.read_all()

        assert result.version == 0
        assert result.events == []

    @pytest.mark.anyio
    async def test_read_all_from_version_at_end(self) -> None:
        """``from_version`` equal to total event count returns an empty
        slice but still reports the correct total version."""
        store = FakeEventStore()

        await store.append_to_stream(
            "cart-a",
            [ItemAddedToCart(item_id="a1", quantity=1)],
            expected_version=0,
        )
        await store.append_to_stream(
            "cart-b",
            [ItemAddedToCart(item_id="b1", quantity=1)],
            expected_version=0,
        )

        result = await store.read_all(from_version=2)

        assert result.version == 2
        assert result.events == []

    @pytest.mark.anyio
    async def test_read_all_from_version_beyond_end(self) -> None:
        """``from_version`` beyond the total event count returns an empty
        slice with the correct version (Python slice bounds are clamped)."""
        store = FakeEventStore()

        await store.append_to_stream(
            "cart-a",
            [ItemAddedToCart(item_id="a1", quantity=1)],
            expected_version=0,
        )

        result = await store.read_all(from_version=10)

        assert result.version == 1
        assert result.events == []


# ===================================================================
# EventStore Protocol Conformance
# ===================================================================


class TestEventStoreProtocol:
    """``FakeEventStore`` must satisfy the ``EventStore`` protocol."""

    def test_isinstance_check_passes(self) -> None:
        """``isinstance(FakeEventStore(), EventStore)`` returns ``True``."""
        store = FakeEventStore()
        assert isinstance(store, EventStore)

    def test_protocol_methods_are_async(self) -> None:
        """All three protocol methods are ``async def`` (confirmed via
        ``inspect.iscoroutinefunction``)."""
        store = FakeEventStore()

        assert inspect.iscoroutinefunction(store.append_to_stream)
        assert inspect.iscoroutinefunction(store.read_stream)
        assert inspect.iscoroutinefunction(store.read_all)


# ===================================================================
# Command ID Dedup
# ===================================================================


class TestCommandDedup:
    """Deduplication of events by ``command_id`` in ``append_to_stream``."""

    @pytest.mark.anyio
    async def test_duplicate_command_id_same_stream_raises(self) -> None:
        """Appending with the same command_id twice to the same stream raises
        ``DuplicateCommandError`` with the correct aggregate_id and
        command_id attributes."""
        store = FakeEventStore()
        command_id = uuid4()

        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="sku-001", quantity=2)],
            expected_version=0,
            command_id=command_id,
        )

        with pytest.raises(
            DuplicateCommandError,
            match="already processed",
        ) as exc:
            await store.append_to_stream(
                "cart-001",
                [ItemAddedToCart(item_id="sku-002", quantity=5)],
                expected_version=1,
                command_id=command_id,
            )

        assert exc.value.aggregate_id == "cart-001"
        assert exc.value.command_id == str(command_id)

    @pytest.mark.anyio
    async def test_same_command_id_different_streams_allowed(self) -> None:
        """The same command_id used on different streams does not raise."""
        store = FakeEventStore()
        command_id = uuid4()

        await store.append_to_stream(
            "cart-a",
            [ItemAddedToCart(item_id="a1", quantity=1)],
            expected_version=0,
            command_id=command_id,
        )
        await store.append_to_stream(
            "cart-b",
            [CartCleared(reason="checkout")],
            expected_version=0,
            command_id=command_id,
        )

        stream_a = await store.read_stream("cart-a")
        stream_b = await store.read_stream("cart-b")
        assert len(stream_a.events) == 1
        assert len(stream_b.events) == 1
        assert stream_a.events[0].item_id == "a1"
        assert isinstance(stream_b.events[0], CartCleared)

    @pytest.mark.anyio
    async def test_command_id_none_preserves_behavior(self) -> None:
        """Appending without a command_id (default None) does not trigger
        dedup tracking; multiple appends to the same stream succeed."""
        store = FakeEventStore()

        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="sku-001", quantity=2)],
            expected_version=0,
        )
        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="sku-002", quantity=5)],
            expected_version=1,
        )

        stream = await store.read_stream("cart-001")
        assert len(stream.events) == 2
        assert stream.events[0].item_id == "sku-001"
        assert stream.events[1].item_id == "sku-002"

    def test_command_dedup_importable(self) -> None:
        """DuplicateCommandError is importable from pydomain.es.exceptions
        and is a subclass of DomainError."""
        assert DuplicateCommandError is not None
        assert issubclass(DuplicateCommandError, DomainError)
        assert issubclass(DuplicateCommandError, Exception)
