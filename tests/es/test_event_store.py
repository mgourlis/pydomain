# mypy: disable-error-code="attr-defined"
"""Conformance tests for the EventStore protocol.

These tests define the behavioral contract that any EventStore implementation
must satisfy. They are written against the protocol interface (using
``isinstance`` with ``@runtime_checkable``) and exercised via the provided
``FakeEventStore``, which serves as the reference implementation.

Non-goals
---------
* Serialization / deserialization  -- the protocol operates on raw
  ``DomainEvent`` objects; storage encoding is an infrastructure concern.
* Performance or concurrency under load.
* Cross-stream atomicity guarantees beyond the documented contract.

Acceptance criteria from DCE-72
-------------------------------
1. ``append_to_stream`` raises ``ConcurrencyError`` on version mismatch.
2. ``read_stream`` returns an ``EventStream`` or raises
   ``StreamNotFoundError``.
3.  Protocol works with ``DomainEvent`` objects (serialization is a storage
   concern).
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from pydomain.ddd import DomainEvent
from pydomain.ddd.exceptions import ConcurrencyError
from pydomain.es.event_store import EventStore
from pydomain.es.event_stream import EventStream
from pydomain.es.exceptions import StreamNotFoundError
from pydomain.testing.fake_event_store import FakeEventStore

# ---------------------------------------------------------------------------
# Module-level DomainEvent subclasses for protocol-agnostic testing
# ---------------------------------------------------------------------------


class ItemAddedToCart(DomainEvent):
    """A test domain event representing adding an item to a cart."""

    item_id: str
    quantity: int


class CartCleared(DomainEvent):
    """A test domain event representing clearing a cart."""

    reason: str = ""


# ===================================================================
# Protocol Type Conformance
# ===================================================================


class TestProtocolConformance:
    """The ``EventStore`` is ``@runtime_checkable`` -- any implementation
    must pass an ``isinstance`` check and expose the expected async
    signatures."""

    def test_fake_event_store_is_instance_of_event_store_protocol(
        self,
    ) -> None:
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

    @pytest.mark.anyio
    async def test_protocol_works_with_domain_event_objects(self) -> None:
        """The protocol accepts raw ``DomainEvent`` objects via
        ``append_to_stream`` and returns them via ``read_stream`` -- no
        serialization round-trip is required."""
        store = FakeEventStore()
        event = ItemAddedToCart(item_id="sku-001", quantity=1)

        # Append a DomainEvent via the protocol
        await store.append_to_stream(
            "cart-001",
            [event],
            expected_version=0,
        )

        # Read it back via the protocol
        stream = await store.read_stream("cart-001")
        returned = stream.events[0]

        # The returned object is a DomainEvent
        assert isinstance(returned, DomainEvent)
        assert isinstance(returned, ItemAddedToCart)
        assert returned.item_id == "sku-001"
        assert returned.quantity == 1


# ===================================================================
# AC1:  ConcurrencyError on version mismatch
# ===================================================================


class TestAppendToStreamConcurrency:
    """AC1 -- ``append_to_stream`` raises ``ConcurrencyError`` when the
    expected version does not match the current stream length."""

    @pytest.mark.anyio
    async def test_concurrency_error_on_wrong_expected_version(
        self,
    ) -> None:
        """Appending with ``expected_version`` that does not match the
        current stream length raises ``ConcurrencyError``.

        Uses ``expected_version=2`` on a stream that has 1 event.
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
    async def test_concurrency_error_with_negative_expected_version(
        self,
    ) -> None:
        """A negative ``expected_version`` never matches the stream length
        (length is always >= 0), so ``ConcurrencyError`` is raised."""
        store = FakeEventStore()

        with pytest.raises(ConcurrencyError):
            await store.append_to_stream(
                "cart-001",
                [ItemAddedToCart(item_id="sku-001", quantity=1)],
                expected_version=-1,
            )

    @pytest.mark.anyio
    async def test_no_concurrency_error_when_version_matches(self) -> None:
        """Appending succeeds without error when ``expected_version``
        matches the current stream length."""
        store = FakeEventStore()

        # Append succeeds with expected_version=0 on a new stream
        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="sku-001", quantity=1)],
            expected_version=0,
        )

        # Subsequent append succeeds with expected_version=1
        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="sku-002", quantity=2)],
            expected_version=1,
        )

        stream = await store.read_stream("cart-001")
        assert len(stream.events) == 2

    @pytest.mark.anyio
    async def test_concurrency_error_on_expected_version_zero_with_existing_stream(
        self,
    ) -> None:
        """When ``expected_version=0`` on an existing stream, the
        implementation raises ``ConcurrencyError`` -- same as any other
        version mismatch."""
        store = FakeEventStore()

        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="sku-001", quantity=1)],
            expected_version=0,
        )

        with pytest.raises(ConcurrencyError):
            await store.append_to_stream(
                "cart-001",
                [ItemAddedToCart(item_id="sku-002", quantity=2)],
                expected_version=0,
            )


# ===================================================================
# AC2:  read_stream returns EventStream or raises StreamNotFoundError
# ===================================================================


class TestReadStreamContract:
    """AC2 -- ``read_stream`` returns an ``EventStream`` for an existing
    stream or raises ``StreamNotFoundError`` for a non-existent one."""

    @pytest.mark.anyio
    async def test_read_stream_returns_event_stream_for_existing_stream(
        self,
    ) -> None:
        """``read_stream`` returns an ``EventStream`` instance for a stream
        that exists."""
        store = FakeEventStore()

        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="sku-001", quantity=1)],
            expected_version=0,
        )

        stream = await store.read_stream("cart-001")
        assert isinstance(stream, EventStream)
        assert len(stream.events) == 1
        assert stream.version == 1

    @pytest.mark.anyio
    async def test_read_stream_raises_stream_not_found_for_missing_stream(
        self,
    ) -> None:
        """``read_stream`` raises ``StreamNotFoundError`` when no events
        have ever been appended for the given ``aggregate_id``."""
        store = FakeEventStore()

        with pytest.raises(StreamNotFoundError, match="not found") as exc:
            await store.read_stream("non-existent-cart")
        assert exc.value.aggregate_id == "non-existent-cart"

    @pytest.mark.anyio
    async def test_read_stream_events_have_domain_event_type(self) -> None:
        """Events returned by ``read_stream`` are ``DomainEvent``
        instances (the protocol does not prescribe serialization)."""
        store = FakeEventStore()

        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="sku-001", quantity=1)],
            expected_version=0,
        )

        stream = await store.read_stream("cart-001")
        for event in stream.events:
            assert isinstance(event, DomainEvent)

    @pytest.mark.anyio
    async def test_read_stream_with_from_version_slice(self) -> None:
        """``from_version`` slices the returned events while ``version``
        still reports the total stream length."""
        store = FakeEventStore()

        for i in range(3):
            await store.append_to_stream(
                "cart-001",
                [ItemAddedToCart(item_id=f"sku-{i:03d}", quantity=i + 1)],
                expected_version=i,
            )

        stream = await store.read_stream("cart-001", from_version=1)
        assert isinstance(stream, EventStream)
        assert len(stream.events) == 2
        assert stream.events[0].item_id == "sku-001"
        assert stream.events[1].item_id == "sku-002"
        # version is the full stream length, not the slice length
        assert stream.version == 3

    @pytest.mark.anyio
    async def test_read_stream_after_concurrency_failure(self) -> None:
        """A failed append (``ConcurrencyError``) does not modify the
        stream, and subsequent reads return the original state."""
        store = FakeEventStore()

        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="sku-001", quantity=1)],
            expected_version=0,
        )

        # Attempt a bad append
        with pytest.raises(ConcurrencyError):
            await store.append_to_stream(
                "cart-001",
                [ItemAddedToCart(item_id="sku-002", quantity=2)],
                expected_version=5,  # wrong
            )

        # Stream state is unchanged
        stream = await store.read_stream("cart-001")
        assert len(stream.events) == 1
        assert stream.events[0].item_id == "sku-001"
        assert stream.version == 1


# ===================================================================
# read_all contract
# ===================================================================


class TestReadAllContract:
    """AC3 -- ``read_all()`` returns a global event log across all streams."""

    @pytest.mark.anyio
    async def test_read_all_returns_event_stream(self) -> None:
        """``read_all`` always returns an ``EventStream`` instance (even
        when the store is empty)."""
        store = FakeEventStore()

        result = await store.read_all()
        assert isinstance(result, EventStream)

    @pytest.mark.anyio
    async def test_read_all_empty_store(self) -> None:
        """An empty store returns ``EventStream(events=[], version=0)``."""
        store = FakeEventStore()

        result = await store.read_all()
        assert result.events == []
        assert result.version == 0

    @pytest.mark.anyio
    async def test_read_all_aggregates_across_streams(self) -> None:
        """Events from multiple streams appear in global append order."""
        store = FakeEventStore()

        await store.append_to_stream(
            "cart-a",
            [ItemAddedToCart(item_id="a1", quantity=1)],
            expected_version=0,
        )
        await store.append_to_stream(
            "cart-b",
            [CartCleared(reason="done")],
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
        assert result.events[0].item_id == "a1"
        assert isinstance(result.events[1], CartCleared)
        assert result.events[2].item_id == "a2"

    @pytest.mark.anyio
    async def test_read_all_respects_from_version(self) -> None:
        """``from_version`` slices the global event log, but ``version``
        still reports the total global event count."""
        store = FakeEventStore()

        for i in range(4):
            await store.append_to_stream(
                f"cart-{i}",
                [ItemAddedToCart(item_id=f"sku-{i:03d}", quantity=1)],
                expected_version=0,
            )

        result = await store.read_all(from_version=2)
        assert result.version == 4
        assert len(result.events) == 2
        assert result.events[0].item_id == "sku-002"
        assert result.events[1].item_id == "sku-003"

    @pytest.mark.anyio
    async def test_read_all_version_is_global_count(self) -> None:
        """``version`` reflects the total number of events across all
        streams, not the number of events returned by the slice."""
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

        full = await store.read_all()
        assert full.version == 2

        sliced = await store.read_all(from_version=1)
        assert sliced.version == 2
        assert len(sliced.events) == 1

    @pytest.mark.anyio
    async def test_read_all_domain_event_types_preserved(self) -> None:
        """The protocol preserves concrete ``DomainEvent`` types through
        ``read_all``."""
        store = FakeEventStore()

        await store.append_to_stream(
            "cart-a",
            [ItemAddedToCart(item_id="sku-001", quantity=1)],
            expected_version=0,
        )
        await store.append_to_stream(
            "cart-b",
            [CartCleared(reason="checkout")],
            expected_version=0,
        )

        result = await store.read_all()
        assert isinstance(result.events[0], ItemAddedToCart)
        assert isinstance(result.events[1], CartCleared)

    @pytest.mark.anyio
    async def test_read_all_returned_objects_are_frozen(self) -> None:
        """The ``EventStream`` returned by ``read_all`` is immutable."""
        store = FakeEventStore()

        await store.append_to_stream(
            "cart-001",
            [ItemAddedToCart(item_id="sku-001", quantity=1)],
            expected_version=0,
        )

        result = await store.read_all()
        with pytest.raises((TypeError, ValidationError)):
            result.version = 99
