"""Tests for EventSourcedAggregateRoot -- _apply(), _replay(), and version tracking.

Verifies that _apply() mutates state AND buffers events while _replay()
mutates state WITHOUT buffering, that pull_events() drains correctly,
and that version tracking is consistent through both paths.
"""

from __future__ import annotations

from typing import cast

import pytest

from pydomain.ddd import DomainEvent
from pydomain.es.aggregate import EventSourcedAggregateRoot

# ---------------------------------------------------------------------------
# Module-level DomainEvent subclasses for testing
# ---------------------------------------------------------------------------


class OrderPlaced(DomainEvent):
    """A test domain event representing order placement."""

    order_id: str
    customer_name: str


class LineItemAdded(DomainEvent):
    """A test domain event representing adding a line item to an order."""

    order_id: str
    item_name: str
    price: float


class OrderCancelled(DomainEvent):
    """A test domain event representing order cancellation."""

    order_id: str
    reason: str


# ---------------------------------------------------------------------------
# Concrete EventSourcedAggregateRoot subclass for testing
# ---------------------------------------------------------------------------


class TestOrder(EventSourcedAggregateRoot[str]):
    """Concrete event-sourced aggregate for testing."""

    customer_name: str = ""
    items: list[dict] = []
    status: str = "new"

    def _when(self, event: DomainEvent) -> None:
        if isinstance(event, OrderPlaced):
            self.customer_name = event.customer_name
            self.status = "placed"
        elif isinstance(event, LineItemAdded):
            self.items.append({"name": event.item_name, "price": event.price})
        elif isinstance(event, OrderCancelled):
            self.status = "cancelled"
        else:
            raise ValueError(f"Unknown event: {event!r}")


# ===================================================================
# Abstract instantiation guard
# ===================================================================


class TestAbstractInstantiation:
    """EventSourcedAggregateRoot cannot be instantiated directly."""

    def test_cannot_instantiate_abstract_aggregate(self) -> None:
        with pytest.raises(TypeError):
            EventSourcedAggregateRoot[str](id="test")  # type: ignore[abstract]


# ===================================================================
# _apply() -- Record an event and mutate state
# ===================================================================


class TestApply:
    """_apply() -- record an event and mutate state."""

    def test_apply_calls_when_and_adds_event(self) -> None:
        """_apply calls _when to update state and _add_event to buffer it,
        so both state changes and pending events are present afterwards."""
        order = TestOrder(id="order-1")
        event = OrderPlaced(order_id="order-1", customer_name="Alice")
        order._apply(event)

        assert order.customer_name == "Alice"
        assert order.status == "placed"

        pending = order.pull_events()
        assert len(pending) == 1
        assert pending[0] is event

    def test_apply_increments_version(self) -> None:
        """Each _apply call increments the aggregate version by 1."""
        order = TestOrder(id="order-1")

        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        assert order.version == 1

        order._apply(LineItemAdded(order_id="order-1", item_name="Widget", price=9.99))
        assert order.version == 2

    def test_apply_multiple_events(self) -> None:
        """Applying multiple events updates version and buffers all of them."""
        order = TestOrder(id="order-1")

        events = [
            OrderPlaced(order_id="order-1", customer_name="Alice"),
            LineItemAdded(order_id="order-1", item_name="Widget", price=9.99),
            LineItemAdded(order_id="order-1", item_name="Gadget", price=5.99),
        ]
        for e in events:
            order._apply(e)

        assert order.version == 3
        pending = order.pull_events()
        assert len(pending) == 3
        assert pending == events

    def test_apply_order_cancelled_sets_status(self) -> None:
        """Applying OrderCancelled transitions status to cancelled."""
        order = TestOrder(id="order-1")
        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))
        order._apply(OrderCancelled(order_id="order-1", reason="Changed mind"))

        assert order.status == "cancelled"

    def test_apply_unknown_event_raises_value_error(self) -> None:
        """Applying a DomainEvent subclass that _when does not handle
        raises ValueError."""
        order = TestOrder(id="order-1")

        class UnknownEvent(DomainEvent):
            pass

        with pytest.raises(ValueError, match="Unknown event"):
            order._apply(UnknownEvent())


# ===================================================================
# _replay() -- Rebuild state from a historical event without buffering
# ===================================================================


class TestReplay:
    """_replay() -- rebuild state from a historical event without buffering."""

    def test_replay_mutates_state_without_buffering(self) -> None:
        """_replay applies state mutations via _when but does NOT buffer
        the event, so pull_events returns an empty list."""
        order = TestOrder(id="order-1")

        order._replay(OrderPlaced(order_id="order-1", customer_name="Alice"))

        assert order.customer_name == "Alice"
        assert order.status == "placed"
        assert order.pull_events() == []

    def test_replay_increments_version(self) -> None:
        """Each _replay call increments the aggregate version by 1."""
        order = TestOrder(id="order-1")

        order._replay(OrderPlaced(order_id="order-1", customer_name="Alice"))
        assert order.version == 1

        order._replay(LineItemAdded(order_id="order-1", item_name="Widget", price=9.99))
        assert order.version == 2

    def test_replay_unknown_event_raises_value_error(self) -> None:
        """Replaying a DomainEvent subclass that _when does not handle
        raises ValueError."""
        order = TestOrder(id="order-1")

        class UnknownEvent(DomainEvent):
            pass

        with pytest.raises(ValueError, match="Unknown event"):
            order._replay(UnknownEvent())


# ===================================================================
# pull_events() -- draining buffered pending events
# ===================================================================


class TestPullEvents:
    """pull_events() -- draining buffered pending events."""

    def test_pull_events_returns_buffered_events(self) -> None:
        """_apply buffers events and pull_events returns them."""
        order = TestOrder(id="order-1")
        event = OrderPlaced(order_id="order-1", customer_name="Alice")
        order._apply(event)

        pending = order.pull_events()
        assert pending == [event]

    def test_pull_events_clears_buffer(self) -> None:
        """After pull_events, the internal buffer is emptied."""
        order = TestOrder(id="order-1")
        order._apply(OrderPlaced(order_id="order-1", customer_name="Alice"))

        order.pull_events()  # drain
        assert order.pull_events() == []

    def test_pull_events_after_replay_only_returns_new(self) -> None:
        """Events applied via _replay are NOT buffered, so pull_events
        only returns events that were added via _apply afterwards."""
        order = TestOrder(id="order-1")

        # Replay two historical events
        order._replay(OrderPlaced(order_id="order-1", customer_name="Alice"))
        order._replay(LineItemAdded(order_id="order-1", item_name="Widget", price=9.99))
        assert order.version == 2
        assert order.pull_events() == []

        # Apply a new event
        order._apply(LineItemAdded(order_id="order-1", item_name="Gadget", price=5.99))
        assert order.version == 3
        pending = order.pull_events()
        assert len(pending) == 1
        assert cast(LineItemAdded, pending[0]).item_name == "Gadget"


# ===================================================================
# Version tracking across _replay and _apply
# ===================================================================


class TestVersionTracking:
    """Version tracking across _replay and _apply."""

    def test_replay_then_apply_continues_version(self) -> None:
        """Version increments seamlessly across _replay and _apply calls."""
        order = TestOrder(id="order-1")

        order._replay(OrderPlaced(order_id="order-1", customer_name="Alice"))
        assert order.version == 1

        order._replay(LineItemAdded(order_id="order-1", item_name="Widget", price=9.99))
        assert order.version == 2

        order._apply(LineItemAdded(order_id="order-1", item_name="Gadget", price=5.99))
        assert order.version == 3

    def test_version_starts_at_zero(self) -> None:
        """A newly created aggregate has version 0."""
        order = TestOrder(id="order-1")
        assert order.version == 0
