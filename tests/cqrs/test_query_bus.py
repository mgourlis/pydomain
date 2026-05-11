"""Tests for the CQRS QueryBus.

Follows the same structural patterns as test_bus.py but adapted for
the read-only query path: no Unit of Work, no events, single typed
result returned directly.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from pydomain.cqrs import (
    HandlerAlreadyRegisteredError,
    NoHandlerRegisteredError,
    Query,
    QueryBus,
    QueryResult,
)
from pydomain.cqrs.behaviors import MessageContext, NextHandler

# ── Sample domain types ─────────────────────────────────────────────────


class GetOrderResult(QueryResult):
    order_id: str
    items: list[str]


class GetOrder(Query[GetOrderResult]):
    order_id: str


class CountItemsResult(QueryResult):
    count: int


class CountItems(Query[CountItemsResult]):
    values: list[str]


# ── Fake handlers (simple async functions) ──────────────────────────────


async def fake_get_order_handler(query: GetOrder) -> GetOrderResult:
    return GetOrderResult(order_id=query.order_id, items=["item-1", "item-2"])


async def fake_count_items_handler(query: CountItems) -> CountItemsResult:
    return CountItemsResult(count=len(query.values))


# ── Spy behavior for pipeline tracing ───────────────────────────────────


class SpyBehavior:
    """Pipeline behavior that records execution order to a shared trace list.

    Appends ``{name}_before`` and ``{name}_after`` to ``trace`` around
    the ``next()`` call so tests can verify ordering.
    """

    def __init__(self, name: str, trace: list[str]) -> None:
        self._name = name
        self._trace = trace

    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        self._trace.append(f"{self._name}_before")
        result = await next()
        self._trace.append(f"{self._name}_after")
        return result


# ── Tests ────────────────────────────────────────────────────────────────


class TestRegister:
    """Registration — basic and duplicate."""

    @pytest.mark.anyio
    async def test_register_handler(self, query_bus: QueryBus) -> None:
        """A handler can be registered for a query type without error."""
        query_bus.register(GetOrder, fake_get_order_handler)

    @pytest.mark.anyio
    async def test_duplicate_registration_raises_error(
        self, query_bus: QueryBus
    ) -> None:
        """Registering a second handler for the same query type raises."""
        query_bus.register(GetOrder, fake_get_order_handler)
        with pytest.raises(HandlerAlreadyRegisteredError):
            query_bus.register(GetOrder, fake_get_order_handler)


class TestDispatch:
    """Dispatch — happy path, errors, signature."""

    @pytest.mark.anyio
    async def test_dispatch_returns_typed_result(self, query_bus: QueryBus) -> None:
        """Dispatch returns the query's typed result with correct data."""
        query_bus.register(GetOrder, fake_get_order_handler)
        query = GetOrder(order_id="ord-123")

        result = await query_bus.dispatch(query)

        assert isinstance(result, GetOrderResult)
        assert result.order_id == "ord-123"
        assert result.items == ["item-1", "item-2"]

    @pytest.mark.anyio
    async def test_unregistered_query_raises_error(self, query_bus: QueryBus) -> None:
        """Dispatching an unregistered query raises NoHandlerRegisteredError."""
        query = GetOrder(order_id="ord-123")

        with pytest.raises(NoHandlerRegisteredError):
            await query_bus.dispatch(query)

    def test_dispatch_has_no_uow_parameter(self) -> None:
        """dispatch() signature has no ``uow`` parameter (read-only path)."""
        sig = inspect.signature(QueryBus.dispatch)
        assert "uow" not in sig.parameters

    @pytest.mark.anyio
    async def test_handler_exception_propagates(self, query_bus: QueryBus) -> None:
        """An exception raised inside the handler propagates through dispatch."""

        async def failing_handler(query: GetOrder) -> GetOrderResult:
            msg = "handler failed"
            raise ValueError(msg)

        query_bus.register(GetOrder, failing_handler)
        query = GetOrder(order_id="ord-123")

        with pytest.raises(ValueError, match="handler failed"):
            await query_bus.dispatch(query)


class TestPipelineBehaviors:
    """Pipeline behavior edge cases: empty list, ordering, short-circuit, exceptions."""

    @pytest.mark.anyio
    async def test_empty_behavior_list_invokes_handler(
        self, query_bus: QueryBus
    ) -> None:
        """An empty behavior list dispatches correctly (zero-overhead path)."""
        query_bus.register(GetOrder, fake_get_order_handler, behaviors=[])
        query = GetOrder(order_id="ord-123")

        result = await query_bus.dispatch(query)

        assert isinstance(result, GetOrderResult)
        assert result.order_id == "ord-123"
        assert result.items == ["item-1", "item-2"]

    @pytest.mark.anyio
    async def test_behaviors_execute_in_order(self, query_bus: QueryBus) -> None:
        """Pipeline behaviors fire in registration order (outer first)."""
        trace: list[str] = []

        query_bus.register(
            CountItems,
            fake_count_items_handler,
            behaviors=[
                SpyBehavior("first", trace),
                SpyBehavior("second", trace),
            ],
        )

        query = CountItems(values=["a", "b", "c"])
        result = await query_bus.dispatch(query)

        assert isinstance(result, CountItemsResult)
        assert result.count == 3
        assert trace == [
            "first_before",
            "second_before",
            "second_after",
            "first_after",
        ]

    @pytest.mark.anyio
    async def test_behavior_can_short_circuit(self, query_bus: QueryBus) -> None:
        """A behavior that does not call next() prevents handler execution."""
        handler_called: bool = False

        class ShortCircuitBehavior:
            async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
                return GetOrderResult(order_id="short-circuited", items=[])

        async def handler(query: GetOrder) -> GetOrderResult:
            nonlocal handler_called
            handler_called = True
            return GetOrderResult(order_id=query.order_id, items=["real-item"])

        query_bus.register(GetOrder, handler, behaviors=[ShortCircuitBehavior()])
        query = GetOrder(order_id="ord-123")

        result = await query_bus.dispatch(query)

        assert isinstance(result, GetOrderResult)
        assert result.order_id == "short-circuited"
        assert not handler_called

    @pytest.mark.anyio
    async def test_behavior_exception_propagates(self, query_bus: QueryBus) -> None:
        """An exception raised in a pipeline behavior propagates through dispatch."""

        class FailingBehavior:
            async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
                msg = "behavior failed"
                raise RuntimeError(msg)

        query_bus.register(
            GetOrder, fake_get_order_handler, behaviors=[FailingBehavior()]
        )
        query = GetOrder(order_id="ord-123")

        with pytest.raises(RuntimeError, match="behavior failed"):
            await query_bus.dispatch(query)


class TestExports:
    """Public API exports."""

    def test_query_bus_in_all(self) -> None:
        """QueryBus is exported in pydomain.cqrs.__all__."""
        from pydomain.cqrs import __all__ as cqrs_all

        assert "QueryBus" in cqrs_all
