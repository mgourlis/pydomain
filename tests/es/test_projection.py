"""Tests for pydomain/es/projection.py -- Projection ABC with event handler dispatch.

Verifies that the Projection ABC dispatches domain events to the correct
``_when_{EventTypeName}`` handler, silently ignores unknown event types,
and that concrete subclasses correctly declare ``name`` and ``version``
ClassVars.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from pydomain.ddd import DomainEvent
from pydomain.es.projection import Projection

# ===================================================================
# Test domain events
# ===================================================================


class OrderPlaced(DomainEvent):
    """A test domain event representing order placement."""

    order_id: str
    customer_name: str


class OrderCancelled(DomainEvent):
    """A test domain event representing order cancellation."""

    order_id: str
    reason: str


class LineItemAdded(DomainEvent):
    """A test domain event representing adding a line item to an order."""

    order_id: str
    item_name: str
    price: float


class OrderShipped(DomainEvent):
    """A test domain event representing order shipment (no handler in spy)."""

    order_id: str


# ===================================================================
# Spy projection -- records which handlers were called
# ===================================================================


class SpyProjection(Projection):
    """Concrete Projection subclass that spies on handler dispatch.

    Records every ``_when_*`` invocation so tests can verify that the
    correct handler was called with the right event instance.
    """

    __test__ = False  # pytest: not a test class despite Test prefix

    name: ClassVar[str] = "test_projection"
    version: ClassVar[int] = 1

    def __init__(self) -> None:
        self.calls: list[tuple[str, DomainEvent]] = []

    async def _when_OrderPlaced(self, event: OrderPlaced) -> None:
        self.calls.append(("_when_OrderPlaced", event))

    async def _when_OrderCancelled(self, event: OrderCancelled) -> None:
        self.calls.append(("_when_OrderCancelled", event))

    async def _when_LineItemAdded(self, event: LineItemAdded) -> None:
        self.calls.append(("_when_LineItemAdded", event))


# ===================================================================
# ClassVar: name and version
# ===================================================================


class TestProjectionClassVars:
    """Concrete subclass declares *name* and *version* ClassVars."""

    def test_class_level_access(self) -> None:
        """name and version are accessible as class attributes."""
        assert SpyProjection.name == "test_projection"
        assert SpyProjection.version == 1

    def test_instance_level_access(self) -> None:
        """name and version are accessible via an instance."""
        proj = SpyProjection()
        assert proj.name == "test_projection"
        assert proj.version == 1


# ===================================================================
# Dispatch: handle() routes to _when_{EventTypeName}
# ===================================================================


class TestHandleDispatch:
    """handle() dispatches to the correct _when_* method."""

    @pytest.mark.anyio
    async def test_dispatch_order_placed(self) -> None:
        """handle(OrderPlaced) calls _when_OrderPlaced."""
        proj = SpyProjection()
        event = OrderPlaced(order_id="order-1", customer_name="Alice")

        await proj.handle(event)

        assert len(proj.calls) == 1
        handler_name, received = proj.calls[0]
        assert handler_name == "_when_OrderPlaced"
        assert received is event

    @pytest.mark.anyio
    async def test_dispatch_order_cancelled(self) -> None:
        """handle(OrderCancelled) calls _when_OrderCancelled, not others."""
        proj = SpyProjection()
        event = OrderCancelled(order_id="order-1", reason="Changed mind")

        await proj.handle(event)

        assert len(proj.calls) == 1
        handler_name, received = proj.calls[0]
        assert handler_name == "_when_OrderCancelled"
        assert received is event

    @pytest.mark.anyio
    async def test_dispatch_line_item_added(self) -> None:
        """handle(LineItemAdded) calls _when_LineItemAdded."""
        proj = SpyProjection()
        event = LineItemAdded(order_id="order-1", item_name="Widget", price=9.99)

        await proj.handle(event)

        assert len(proj.calls) == 1
        handler_name, received = proj.calls[0]
        assert handler_name == "_when_LineItemAdded"
        assert received is event

    @pytest.mark.anyio
    async def test_only_correct_handler_is_called(self) -> None:
        """Sending OrderPlaced does NOT call _when_OrderCancelled."""
        proj = SpyProjection()
        event = OrderPlaced(order_id="order-1", customer_name="Alice")

        await proj.handle(event)

        assert len(proj.calls) == 1
        assert proj.calls[0][0] == "_when_OrderPlaced"


# ===================================================================
# Unknown event types are silently ignored
# ===================================================================


class TestUnknownEvent:
    """Events without a matching _when_* handler are silently ignored."""

    @pytest.mark.anyio
    async def test_unknown_event_no_handler(self) -> None:
        """Sending an event with no matching handler raises no error."""
        proj = SpyProjection()
        event = OrderShipped(order_id="order-1")

        await proj.handle(event)  # should not raise

        assert proj.calls == []

    @pytest.mark.anyio
    async def test_unknown_event_does_not_raise(self) -> None:
        """No exception for a base DomainEvent without a handler."""
        proj = SpyProjection()

        await proj.handle(DomainEvent())  # no _when_DomainEvent handler

        assert proj.calls == []

    @pytest.mark.anyio
    async def test_known_events_still_work_after_unknown(self) -> None:
        """Unknown events don't break subsequent dispatch of known events."""
        proj = SpyProjection()
        unknown = OrderShipped(order_id="order-1")
        known = OrderPlaced(order_id="order-2", customer_name="Bob")

        await proj.handle(unknown)
        await proj.handle(known)

        assert len(proj.calls) == 1
        assert proj.calls[0][0] == "_when_OrderPlaced"
        assert proj.calls[0][1] is known


# ===================================================================
# Multiple event type handlers
# ===================================================================


class TestMultipleHandlers:
    """Projection can handle multiple distinct event types."""

    @pytest.mark.anyio
    async def test_multiple_event_types_dispatched_correctly(self) -> None:
        """A sequence of different events dispatches each to the right handler."""
        proj = SpyProjection()

        events = [
            OrderPlaced(order_id="order-1", customer_name="Alice"),
            LineItemAdded(order_id="order-1", item_name="Widget", price=9.99),
            OrderCancelled(order_id="order-1", reason="Changed mind"),
        ]

        for e in events:
            await proj.handle(e)

        assert len(proj.calls) == 3
        assert proj.calls[0] == ("_when_OrderPlaced", events[0])
        assert proj.calls[1] == ("_when_LineItemAdded", events[1])
        assert proj.calls[2] == ("_when_OrderCancelled", events[2])

    @pytest.mark.anyio
    async def test_handler_state_is_independent(self) -> None:
        """Each handler call is independent; subsequent calls add new records."""
        proj = SpyProjection()

        await proj.handle(OrderPlaced(order_id="order-1", customer_name="Alice"))
        assert len(proj.calls) == 1

        await proj.handle(OrderPlaced(order_id="order-2", customer_name="Bob"))
        assert len(proj.calls) == 2

        assert proj.calls[0][0] == "_when_OrderPlaced"
        assert proj.calls[1][0] == "_when_OrderPlaced"


# ===================================================================
# Handler receives concrete event instance
# ===================================================================


class TestConcreteEventInstance:
    """Handler receives the full concrete event, not a base DomainEvent."""

    @pytest.mark.anyio
    async def test_order_placed_has_concrete_attributes(self) -> None:
        """The event passed to _when_OrderPlaced has OrderPlaced fields."""
        proj = SpyProjection()
        event = OrderPlaced(order_id="order-1", customer_name="Alice")

        await proj.handle(event)

        _, received = proj.calls[0]
        assert isinstance(received, OrderPlaced)
        assert received.order_id == "order-1"
        assert received.customer_name == "Alice"

    @pytest.mark.anyio
    async def test_order_cancelled_has_concrete_attributes(self) -> None:
        """The event passed to _when_OrderCancelled has OrderCancelled fields."""
        proj = SpyProjection()
        event = OrderCancelled(order_id="order-1", reason="Out of stock")

        await proj.handle(event)

        _, received = proj.calls[0]
        assert isinstance(received, OrderCancelled)
        assert received.order_id == "order-1"
        assert received.reason == "Out of stock"

    @pytest.mark.anyio
    async def test_line_item_added_has_concrete_attributes(self) -> None:
        """The event passed to _when_LineItemAdded has LineItemAdded fields."""
        proj = SpyProjection()
        event = LineItemAdded(order_id="order-1", item_name="Gadget", price=5.99)

        await proj.handle(event)

        _, received = proj.calls[0]
        assert isinstance(received, LineItemAdded)
        assert received.order_id == "order-1"
        assert received.item_name == "Gadget"
        assert received.price == 5.99

    @pytest.mark.anyio
    async def test_same_event_instance_passed_to_handler(self) -> None:
        """The exact event object passed to handle() is forwarded to _when_*."""
        proj = SpyProjection()
        event = OrderPlaced(order_id="order-1", customer_name="Alice")

        await proj.handle(event)

        _, received = proj.calls[0]
        assert received is event


# ===================================================================
# Real-world usage scenario
# ===================================================================


class TestOrderProjectionScenario:
    """End-to-end scenario: an order projection processing a typical event stream."""

    @pytest.mark.anyio
    async def test_order_lifecycle_dispatch(self) -> None:
        """A realistic order lifecycle dispatches correctly through handle()."""
        proj = SpyProjection()

        events = [
            OrderPlaced(order_id="order-1", customer_name="Alice"),
            LineItemAdded(order_id="order-1", item_name="Widget", price=9.99),
            LineItemAdded(order_id="order-1", item_name="Gadget", price=5.99),
            OrderShipped(order_id="order-1"),  # unknown — should be ignored
            OrderCancelled(order_id="order-1", reason="Delivered"),
        ]

        for e in events:
            await proj.handle(e)

        # Only OrderPlaced, LineItemAdded (x2), and OrderCancelled are handled
        assert len(proj.calls) == 4
        assert proj.calls[0][0] == "_when_OrderPlaced"
        assert proj.calls[1][0] == "_when_LineItemAdded"
        assert proj.calls[2][0] == "_when_LineItemAdded"
        assert proj.calls[3][0] == "_when_OrderCancelled"

        # Verify concrete types and values
        assert isinstance(proj.calls[0][1], OrderPlaced)
        assert proj.calls[0][1].customer_name == "Alice"

        assert isinstance(proj.calls[1][1], LineItemAdded)
        assert proj.calls[1][1].item_name == "Widget"

        assert isinstance(proj.calls[2][1], LineItemAdded)
        assert proj.calls[2][1].item_name == "Gadget"

        assert isinstance(proj.calls[3][1], OrderCancelled)
        assert proj.calls[3][1].reason == "Delivered"
