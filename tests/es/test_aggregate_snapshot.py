"""Tests for ``EventSourcedAggregateRoot._take_snapshot()``.

Verifies that ``_take_snapshot()`` captures the correct aggregate state as a
``Snapshot``, does **not** mutate aggregate state, and excludes internal
fields and the ``version`` field from the state dict.
"""

from __future__ import annotations

from pydomain.es.snapshot import Snapshot
from tests.es.conftest import LineItemAdded, OrderPlaced, TestOrder

# ===================================================================
# _take_snapshot() -- Capture aggregate state as a Snapshot
# ===================================================================


class TestTakeSnapshot:
    """``_take_snapshot()`` captures aggregate state as a ``Snapshot``."""

    def test_after_one_event_returns_correct_snapshot(self) -> None:
        """After one ``_apply(OrderPlaced(...))``, returns a ``Snapshot`` with
        ``aggregate_id`` matching ``str(order.id)``, ``version == 1``, and
        ``state`` containing ``customer_name`` from the event."""
        order = TestOrder(id="order-001")
        order._apply(OrderPlaced(order_id="order-001", customer_name="Alice"))

        snap = order._take_snapshot()

        assert isinstance(snap, Snapshot)
        assert snap.aggregate_id == "order-001"
        assert snap.version == 1
        assert snap.state["customer_name"] == "Alice"
        assert snap.state["status"] == "placed"

    def test_after_multiple_events_version_and_state_accumulated(
        self,
    ) -> None:
        """After multiple events, version matches the total event count and
        state reflects all accumulated mutations."""
        order = TestOrder(id="order-002")
        order._apply(OrderPlaced(order_id="order-002", customer_name="Bob"))
        order._apply(
            LineItemAdded(order_id="order-002", item_name="Widget", price=9.99)
        )
        order._apply(
            LineItemAdded(order_id="order-002", item_name="Gadget", price=5.99)
        )

        snap = order._take_snapshot()

        assert snap.version == 3
        assert snap.state["customer_name"] == "Bob"
        assert snap.state["status"] == "placed"
        assert snap.state["items"] == [
            {"name": "Widget", "price": 9.99},
            {"name": "Gadget", "price": 5.99},
        ]

    def test_does_not_mutate_aggregate(self) -> None:
        """``_take_snapshot()`` does **not** mutate the aggregate -- version
        and pending events remain unchanged."""
        order = TestOrder(id="order-003")
        order._apply(OrderPlaced(order_id="order-003", customer_name="Charlie"))

        version_before = order.version
        order._take_snapshot()

        # Version unchanged
        assert order.version == version_before
        assert order.version == 1

        # Pending events still present (not drained)
        pending = order.pull_events()
        assert len(pending) == 1

    def test_called_twice_returns_distinct_snapshots(self) -> None:
        """Calling ``_take_snapshot()`` twice returns two distinct
        ``Snapshot`` instances with equal values (ignoring created_at)."""
        order = TestOrder(id="order-004")
        order._apply(OrderPlaced(order_id="order-004", customer_name="Diana"))

        snap1 = order._take_snapshot()
        snap2 = order._take_snapshot()

        assert snap1 is not snap2
        assert snap1.aggregate_id == snap2.aggregate_id
        assert snap1.version == snap2.version
        assert snap1.state == snap2.state

    def test_state_excludes_internal_fields(self) -> None:
        """The state dict includes all public model fields but NOT internal
        fields (like ``_pending_events``)."""
        order = TestOrder(id="order-005")
        order._apply(OrderPlaced(order_id="order-005", customer_name="Eve"))
        order._apply(
            LineItemAdded(order_id="order-005", item_name="Widget", price=9.99)
        )

        snap = order._take_snapshot()

        # Internal/private fields are excluded
        assert "_pending_events" not in snap.state

        # All public model fields are present
        assert "id" in snap.state
        assert "customer_name" in snap.state
        assert "items" in snap.state
        assert "status" in snap.state

    def test_state_excludes_version_field(self) -> None:
        """The state dict does **not** contain ``version`` (removed by
        ``_take_snapshot`` before building the ``Snapshot``)."""
        order = TestOrder(id="order-006")
        order._apply(OrderPlaced(order_id="order-006", customer_name="Frank"))

        snap = order._take_snapshot()

        assert "version" not in snap.state
        # Version is available at the Snapshot top level
        assert snap.version == 1
