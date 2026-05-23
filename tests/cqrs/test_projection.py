"""Tests for the Projection infrastructure.

Covers the Projection and ProjectionStore runtime-checkable protocols,
the InMemoryProjectionStore testing fake, and an integration test of a
concrete projection together with the store.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from pydantic import BaseModel

from pydomain.cqrs.projection import Projection, ProjectionStore
from pydomain.ddd import DomainEvent
from pydomain.testing import InMemoryProjectionStore

# ---------------------------------------------------------------------------
# Module-level helper types
# ---------------------------------------------------------------------------


class OrderSummaryState(BaseModel):
    """Read model state for the OrderSummary projection."""

    total_orders: int = 0
    total_amount: float = 0.0


class OrderPlaced(DomainEvent):
    """A test domain event representing an order being placed."""

    order_amount: float


class OrderCancelled(DomainEvent):
    """A test domain event representing an order being cancelled."""

    order_id: str = ""


class FullProjection:
    """A class that fully implements the ``Projection`` protocol (both
    members: ``apply`` and ``rebuild``)."""

    async def apply(self, event: DomainEvent) -> None:
        pass  # no-op for protocol conformance test

    async def rebuild(self, events: Sequence[DomainEvent]) -> None:
        for event in events:
            await self.apply(event)


class PartialProjection:
    """A class that implements only *some* ``Projection`` protocol members.
    ``rebuild`` is intentionally omitted so that the isinstance check fails."""

    async def apply(self, event: DomainEvent) -> None: ...


class OrderSummaryProjection:
    """Concrete projection implementing the left-fold pattern for testing.

    Transforms ``OrderPlaced`` events into an ``OrderSummaryState``
    read model.  Non-``OrderPlaced`` events are silently ignored.

    This is a pure CQRS projection — no checkpoint tracking (that
    belongs in the ES layer).
    """

    def __init__(self) -> None:
        self._state = OrderSummaryState()
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    @property
    def state(self) -> OrderSummaryState:
        return self._state

    async def apply(self, event: DomainEvent) -> None:
        if isinstance(event, OrderPlaced):
            self._count += 1
            self._state = OrderSummaryState(
                total_orders=self._state.total_orders + 1,
                total_amount=self._state.total_amount + event.order_amount,
            )

    async def rebuild(self, events: Sequence[DomainEvent]) -> None:
        self._state = OrderSummaryState()
        self._count = 0
        for event in events:
            await self.apply(event)


# ===================================================================
# Projection Protocol Conformance
# ===================================================================


class TestProjectionProtocol:
    """``isinstance`` checks for the ``Projection`` runtime-checkable
    protocol."""

    def test_full_implementation_passes_isinstance(self) -> None:
        """A class implementing ``apply`` and ``rebuild`` passes
        ``isinstance(..., Projection)``."""
        proj = FullProjection()
        assert isinstance(proj, Projection)

    def test_partial_implementation_fails_isinstance(self) -> None:
        """A class missing one of the protocol members (``rebuild``) does
        NOT pass the isinstance check."""
        proj = PartialProjection()
        assert not isinstance(proj, Projection)


# ===================================================================
# ProjectionStore Protocol Conformance
# ===================================================================


class TestProjectionStoreProtocol:
    """``isinstance`` checks for the ``ProjectionStore`` runtime-checkable
    protocol."""

    @pytest.mark.anyio
    async def test_in_memory_store_passes_isinstance(self) -> None:
        """``InMemoryProjectionStore`` passes
        ``isinstance(..., ProjectionStore)``."""
        store = InMemoryProjectionStore()
        assert isinstance(store, ProjectionStore)


# ===================================================================
# InMemoryProjectionStore -- Load
# ===================================================================


class TestLoad:
    """``load()`` -- retrieving saved projection state from the in-memory
    store."""

    @pytest.mark.anyio
    async def test_load_returns_none_for_unknown_id(self) -> None:
        """Loading a projection_id that has never been saved returns
        ``None``."""
        store = InMemoryProjectionStore()
        result = await store.load("nonexistent")
        assert result is None

    @pytest.mark.anyio
    async def test_load_returns_saved_state(self) -> None:
        """After ``save(id, state)``, ``load(id)`` returns the exact
        state that was saved."""
        store = InMemoryProjectionStore()
        state: dict[str, Any] = {"total": 42}
        await store.save("orders", state)

        result = await store.load("orders")
        assert result is not None
        assert result == {"total": 42}

    @pytest.mark.anyio
    async def test_load_returns_most_recent_save(self) -> None:
        """Saving the same projection_id twice returns the state from the
        latest save."""
        store = InMemoryProjectionStore()
        await store.save("orders", {"total": 10})
        await store.save("orders", {"total": 99})

        result = await store.load("orders")
        assert result is not None
        assert result == {"total": 99}


# ===================================================================
# InMemoryProjectionStore -- Save
# ===================================================================


class TestSave:
    """``save()`` -- persisting projection state in the in-memory store."""

    @pytest.mark.anyio
    async def test_save_persists_state(self) -> None:
        """After save, load returns the state value that was passed."""
        store = InMemoryProjectionStore()
        state: dict[str, Any] = {"items": ["a", "b"]}

        await store.save("cart-summary", state)
        result = await store.load("cart-summary")
        assert result is not None
        assert result == {"items": ["a", "b"]}

    @pytest.mark.anyio
    async def test_save_overwrites_existing(self) -> None:
        """Saving again with the same projection_id replaces the previous
        state."""
        store = InMemoryProjectionStore()
        await store.save("metrics", {"count": 1})
        await store.save("metrics", {"count": 2})

        result = await store.load("metrics")
        assert result is not None
        assert result == {"count": 2}

    @pytest.mark.anyio
    async def test_save_with_custom_id_type(self) -> None:
        """projection_id can be any string value (short, dashed, dotted,
        namespaced)."""
        store = InMemoryProjectionStore()

        ids = [
            "simple",
            "orders-summary",
            "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "namespace:projection",
        ]
        for i, pid in enumerate(ids):
            await store.save(pid, {"index": i})

        for i, pid in enumerate(ids):
            result = await store.load(pid)
            assert result is not None
            assert result == {"index": i}


# ===================================================================
# Integration: Projection + InMemoryProjectionStore
# ===================================================================


class TestProjectionIntegration:
    """Integration tests using ``OrderSummaryProjection`` together with
    ``InMemoryProjectionStore``."""

    @pytest.mark.anyio
    async def test_projection_applies_relevant_event(self) -> None:
        """Applying an ``OrderPlaced`` event increments the count
        and updates the ``total_orders`` and ``total_amount`` counters."""
        proj = OrderSummaryProjection()
        event = OrderPlaced(order_amount=100.0)

        await proj.apply(event)

        assert proj.count == 1
        assert proj.state.total_orders == 1
        assert proj.state.total_amount == 100.0

    @pytest.mark.anyio
    async def test_projection_ignores_irrelevant_event(self) -> None:
        """Applying a different event type (``OrderCancelled``) leaves
        the projection state unchanged."""
        proj = OrderSummaryProjection()
        event = OrderCancelled(order_id="ord-001")

        await proj.apply(event)

        assert proj.count == 0
        assert proj.state.total_orders == 0
        assert proj.state.total_amount == 0.0

    @pytest.mark.anyio
    async def test_rebuild_from_events(self) -> None:
        """Rebuilding from a list of ``OrderPlaced`` events produces the
        correct final state with aggregated totals."""
        proj = OrderSummaryProjection()
        events = [
            OrderPlaced(order_amount=50.0),
            OrderPlaced(order_amount=25.0),
            OrderPlaced(order_amount=100.0),
        ]

        await proj.rebuild(events)

        assert proj.count == 3
        assert proj.state.total_orders == 3
        assert proj.state.total_amount == 175.0

    @pytest.mark.anyio
    async def test_rebuild_resets_state(self) -> None:
        """Rebuilding after prior state wipes the old state and replays
        only the new events."""
        proj = OrderSummaryProjection()
        await proj.apply(OrderPlaced(order_amount=10.0))

        await proj.rebuild([OrderPlaced(order_amount=200.0)])

        # Prior event should not have carried over
        assert proj.count == 1
        assert proj.state.total_orders == 1
        assert proj.state.total_amount == 200.0

    @pytest.mark.anyio
    async def test_rebuild_empty_events(self) -> None:
        """Rebuilding with an empty event list resets the state to
        defaults and sets count to 0."""
        proj = OrderSummaryProjection()
        await proj.apply(OrderPlaced(order_amount=50.0))

        await proj.rebuild([])

        assert proj.count == 0
        assert proj.state.total_orders == 0
        assert proj.state.total_amount == 0.0

    @pytest.mark.anyio
    async def test_store_save_and_load_projection_state(self) -> None:
        """Saving a projection's state to the store and loading it back
        preserves the values."""
        proj = OrderSummaryProjection()
        await proj.apply(OrderPlaced(order_amount=75.0))

        store = InMemoryProjectionStore()
        await store.save("order-summary", proj.state)

        result = await store.load("order-summary")
        assert result is not None
        assert isinstance(result, OrderSummaryState)
        assert result.total_orders == 1
        assert result.total_amount == 75.0

    @pytest.mark.anyio
    async def test_projections_are_runtime_checkable(self) -> None:
        """``OrderSummaryProjection`` passes
        ``isinstance(..., Projection)`` at runtime."""
        proj = OrderSummaryProjection()
        assert isinstance(proj, Projection)

    @pytest.mark.anyio
    async def test_projection_store_is_runtime_checkable(self) -> None:
        """``InMemoryProjectionStore`` passes
        ``isinstance(..., ProjectionStore)`` at runtime."""
        store = InMemoryProjectionStore()
        assert isinstance(store, ProjectionStore)

    @pytest.mark.anyio
    async def test_multiple_projections_isolation(self) -> None:
        """Two projections with different IDs stored in the same store
        do not interfere with each other's state."""
        store = InMemoryProjectionStore()

        proj_a = OrderSummaryProjection()
        await proj_a.apply(OrderPlaced(order_amount=50.0))
        proj_b = OrderSummaryProjection()
        await proj_b.apply(OrderPlaced(order_amount=100.0))
        await proj_b.apply(OrderPlaced(order_amount=25.0))

        await store.save("proj-a", proj_a.state)
        await store.save("proj-b", proj_b.state)

        result_a = await store.load("proj-a")
        result_b = await store.load("proj-b")
        assert result_a is not None
        assert result_b is not None

        assert isinstance(result_a, OrderSummaryState)
        assert isinstance(result_b, OrderSummaryState)
        assert result_a.total_orders == 1
        assert result_a.total_amount == 50.0
        assert result_b.total_orders == 2
        assert result_b.total_amount == 125.0
