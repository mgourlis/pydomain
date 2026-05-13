"""Tests for the EventStream model -- the frozen read-only representation
of an event stream slice returned by the EventStore protocol.

Covers construction, immutability, equality, and the relationship between
the events list and stream version number.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pydomain.ddd import DomainEvent
from pydomain.es.models import EventStream

# ---------------------------------------------------------------------------
# Module-level DomainEvent subclass for testing
# ---------------------------------------------------------------------------


class ItemAddedToCart(DomainEvent):
    """A test domain event representing adding an item to a cart."""

    item_id: str
    quantity: int


# ===================================================================
# Construction
# ===================================================================


class TestConstruction:
    """Building EventStream instances with various arguments."""

    def test_with_events_and_version(self) -> None:
        """Constructing with a non-empty events list and a version stores
        both correctly."""
        event = ItemAddedToCart(item_id="sku-001", quantity=2)
        stream = EventStream(events=[event], version=1)

        assert stream.events == [event]
        assert stream.version == 1

    def test_empty_events_list_with_version_zero(self) -> None:
        """Constructing with an empty events list and version 0 is valid."""
        stream = EventStream(events=[], version=0)

        assert stream.events == []
        assert stream.version == 0

    def test_version_tracks_stream_length(self) -> None:
        """The version number matches the number of events in the stream."""
        events = [
            ItemAddedToCart(item_id="sku-001", quantity=1),
            ItemAddedToCart(item_id="sku-002", quantity=2),
        ]
        stream = EventStream(events=events, version=2)

        assert stream.version == 2
        assert len(stream.events) == 2


# ===================================================================
# Immutability
# ===================================================================


class TestImmutability:
    """EventStream is frozen -- attribute assignment must raise."""

    def test_cannot_set_events_after_construction(self) -> None:
        """Assigning to ``events`` raises an error because the model is
        frozen."""
        stream = EventStream(events=[], version=0)

        with pytest.raises((TypeError, ValidationError)):
            stream.events = [ItemAddedToCart(item_id="x", quantity=1)]  # type: ignore[misc]

    def test_cannot_set_version_after_construction(self) -> None:
        """Assigning to ``version`` raises an error because the model is
        frozen."""
        stream = EventStream(events=[], version=0)

        with pytest.raises((TypeError, ValidationError)):
            stream.version = 1  # type: ignore[misc]


# ===================================================================
# Equality
# ===================================================================


class TestEquality:
    """EventStream equality is based on events content and version."""

    def test_equal_when_same_events_and_version(self) -> None:
        """Two streams with identical events and version are equal."""
        event = ItemAddedToCart(item_id="sku-001", quantity=2)
        stream1 = EventStream(events=[event], version=1)
        stream2 = EventStream(events=[event], version=1)

        assert stream1 == stream2

    def test_not_equal_when_different_version(self) -> None:
        """Two streams with the same events but different versions are
        not equal."""
        event = ItemAddedToCart(item_id="sku-001", quantity=2)
        stream1 = EventStream(events=[event], version=1)
        stream2 = EventStream(events=[event], version=2)

        assert stream1 != stream2

    def test_not_equal_when_different_events(self) -> None:
        """Two streams with the same version but different events are
        not equal."""
        event_a = ItemAddedToCart(item_id="sku-001", quantity=2)
        event_b = ItemAddedToCart(item_id="sku-002", quantity=5)
        stream1 = EventStream(events=[event_a], version=1)
        stream2 = EventStream(events=[event_b], version=1)

        assert stream1 != stream2
