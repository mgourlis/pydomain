"""Comprehensive tests for the MessageBus facade.

Covers registration (DCE-41), dispatch (DCE-42), and event handler
orchestration (DCE-43).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

import pytest

from pydomain.cqrs import (
    Command,
    CommandBus,
    CommandExecutionError,
    CommandResult,
    EmptyCommandResult,
    NoHandlerRegisteredError,
    Query,
    QueryBus,
    QueryResult,
)
from pydomain.cqrs.behaviors import MessageContext, NextHandler
from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure import MessageBus
from pydomain.testing import FakeRepository, FakeUnitOfWork

# ── Sample test types (prefixed with underscore so pytest doesn't collect them) ─


class _Cmd(Command[EmptyCommandResult]):
    data: str


class _Evt(DomainEvent):
    data: str


class _Res(CommandResult):
    success: bool


class _CmdWithRes(Command[_Res]):
    data: str


class _QryRes(QueryResult):
    value: str


class _Qry(Query[_QryRes]):
    data: str


# ── Helpers ──


async def _store_event(event: DomainEvent) -> FakeUnitOfWork:
    """Create a FakeUnitOfWork whose aggregate produces *event* on commit."""

    class _Agg(AggregateRoot[UUID]):
        pass

    agg = _Agg(id=uuid4())
    agg._add_event(event)
    repo: FakeRepository[_Agg, UUID] = FakeRepository()
    await repo.add(agg)
    return FakeUnitOfWork(repository=repo)


# ════════════════════════════════════════════════════════════════════════════
# DCE-41: Registration
# ════════════════════════════════════════════════════════════════════════════


class TestRegistration:
    """MessageBus registration methods delegate correctly."""

    @pytest.mark.anyio
    async def test_creates_internal_buses(self) -> None:
        """MessageBus() creates internal CommandBus and QueryBus if not provided."""
        bus = MessageBus()
        assert isinstance(bus._command_bus, CommandBus)
        assert isinstance(bus._query_bus, QueryBus)

    @pytest.mark.anyio
    async def test_custom_buses_are_respected(self) -> None:
        """Pre-configured CommandBus and QueryBus are used when provided."""
        cmd_bus = CommandBus()
        qry_bus = QueryBus()
        bus = MessageBus(command_bus=cmd_bus, query_bus=qry_bus)
        assert bus._command_bus is cmd_bus
        assert bus._query_bus is qry_bus

    @pytest.mark.anyio
    async def test_register_command_delegates(
        self,
        bus: MessageBus,
        uow: FakeUnitOfWork,
    ) -> None:
        """register_command() delegates to CommandBus.register()."""
        handler_called: list[bool] = [False]

        async def handler(cmd: _Cmd) -> EmptyCommandResult:
            handler_called[0] = True
            return EmptyCommandResult()

        bus.register_command(_Cmd, handler)
        result = await bus.handle(_Cmd(data="hello"), uow)

        assert isinstance(result, EmptyCommandResult)
        assert handler_called[0]

    @pytest.mark.anyio
    async def test_register_command_with_behaviors(
        self,
        bus: MessageBus,
        uow: FakeUnitOfWork,
    ) -> None:
        """register_command() passes behaviors through to CommandBus."""
        trace: list[str] = []

        class SpyBehavior:
            async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
                trace.append("before")
                result = await next()
                trace.append("after")
                return result

        async def handler(cmd: _Cmd) -> EmptyCommandResult:
            trace.append("handler")
            return EmptyCommandResult()

        bus.register_command(_Cmd, handler, behaviors=[SpyBehavior()])
        result = await bus.handle(_Cmd(data="hello"), uow)

        assert isinstance(result, EmptyCommandResult)
        assert trace == ["before", "handler", "after"]

    @pytest.mark.anyio
    async def test_register_command_raises_on_duplicate(
        self,
        bus: MessageBus,
    ) -> None:
        """register_command() raises when registering a duplicate command type."""
        from pydomain.cqrs import HandlerAlreadyRegisteredError

        async def handler1(cmd: _Cmd) -> EmptyCommandResult:
            return EmptyCommandResult()

        async def handler2(cmd: _Cmd) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register_command(_Cmd, handler1)
        with pytest.raises(HandlerAlreadyRegisteredError):
            bus.register_command(_Cmd, handler2)

    @pytest.mark.anyio
    async def test_register_query_delegates(self, bus: MessageBus) -> None:
        """register_query() delegates to QueryBus.register()."""
        handler_called: list[bool] = [False]

        async def handler(query: _Qry) -> _QryRes:
            handler_called[0] = True
            return _QryRes(value=query.data)

        bus.register_query(_Qry, handler)
        result = await bus.query(_Qry(data="test"))

        assert isinstance(result, _QryRes)
        assert result.value == "test"
        assert handler_called[0]

    @pytest.mark.anyio
    async def test_register_query_with_behaviors(self, bus: MessageBus) -> None:
        """register_query() passes behaviors through to QueryBus."""
        trace: list[str] = []

        class SpyBehavior:
            async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
                trace.append("before")
                result = await next()
                trace.append("after")
                return result

        async def handler(query: _Qry) -> _QryRes:
            trace.append("handler")
            return _QryRes(value=query.data)

        bus.register_query(_Qry, handler, behaviors=[SpyBehavior()])
        result = await bus.query(_Qry(data="test"))

        assert isinstance(result, _QryRes)
        assert result.value == "test"
        assert trace == ["before", "handler", "after"]

    @pytest.mark.anyio
    async def test_register_query_raises_on_duplicate(self, bus: MessageBus) -> None:
        """register_query() raises when registering a duplicate query type."""
        from pydomain.cqrs import HandlerAlreadyRegisteredError

        async def handler(query: _Qry) -> _QryRes:
            return _QryRes(value=query.data)

        bus.register_query(_Qry, handler)
        with pytest.raises(HandlerAlreadyRegisteredError):
            bus.register_query(_Qry, handler)

    @pytest.mark.anyio
    async def test_register_event_supports_multiple(self, bus: MessageBus) -> None:
        """register_event() supports multiple handlers per event type."""
        results: list[str] = []

        async def handler1(event: _Evt) -> None:
            results.append("handler1")

        async def handler2(event: _Evt) -> None:
            results.append("handler2")

        async def cmd_handler(cmd: _Cmd) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register_command(_Cmd, cmd_handler)
        bus.register_event(_Evt, handler1)
        bus.register_event(_Evt, handler2)

        uow = await _store_event(_Evt(data="test"))
        await bus.handle(_Cmd(data="test"), uow)

        assert results == ["handler1", "handler2"]

    @pytest.mark.anyio
    async def test_behaviors_on_event_handlers(self, bus: MessageBus) -> None:
        """Behaviors on event handlers pass through on dispatch."""
        trace: list[str] = []

        class RecordingBehavior:
            async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
                trace.append("behavior_before")
                result = await next()
                trace.append("behavior_after")
                return result

        async def handler(event: _Evt) -> None:
            trace.append("handler")

        async def cmd_handler(cmd: _Cmd) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register_command(_Cmd, cmd_handler)
        bus.register_event(_Evt, handler, behaviors=[RecordingBehavior()])

        uow = await _store_event(_Evt(data="test"))
        await bus.handle(_Cmd(data="test"), uow)

        assert trace == ["behavior_before", "handler", "behavior_after"]

    @pytest.mark.anyio
    async def test_multiple_handlers_each_with_behaviors(
        self,
        bus: MessageBus,
    ) -> None:
        """Multiple handlers for same event each get their own pipeline."""
        trace: list[str] = []

        class PrefixBehavior:
            def __init__(self, prefix: str) -> None:
                self._prefix = prefix

            async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
                trace.append(f"{self._prefix}_before")
                result = await next()
                trace.append(f"{self._prefix}_after")
                return result

        async def handler1(event: _Evt) -> None:
            trace.append("handler1")

        async def handler2(event: _Evt) -> None:
            trace.append("handler2")

        async def cmd_handler(cmd: _Cmd) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register_command(_Cmd, cmd_handler)
        bus.register_event(
            _Evt,
            handler1,
            behaviors=[PrefixBehavior("a")],
        )
        bus.register_event(
            _Evt,
            handler2,
            behaviors=[PrefixBehavior("b")],
        )

        uow = await _store_event(_Evt(data="test"))
        await bus.handle(_Cmd(data="test"), uow)

        assert trace == [
            "a_before",
            "handler1",
            "a_after",
            "b_before",
            "handler2",
            "b_after",
        ]

    def test_message_bus_exported(self) -> None:
        """MessageBus is exported from pydomain.infrastructure."""
        from pydomain.infrastructure import __all__ as infra_all

        assert "MessageBus" in infra_all


# ════════════════════════════════════════════════════════════════════════════
# DCE-42: Dispatch
# ════════════════════════════════════════════════════════════════════════════


class TestDispatch:
    """MessageBus handle() and query() dispatch."""

    @pytest.mark.anyio
    async def test_handle_command_returns_typed_result(
        self,
        bus: MessageBus,
        uow: FakeUnitOfWork,
    ) -> None:
        """handle(command, uow) returns typed CommandResult."""

        async def handler(cmd: _CmdWithRes) -> _Res:
            return _Res(success=True)

        bus.register_command(_CmdWithRes, handler)

        result = await bus.handle(_CmdWithRes(data="hello"), uow)

        assert isinstance(result, _Res)
        assert result.success is True

    @pytest.mark.anyio
    async def test_handle_command_dispatches_collected_events(
        self,
        bus: MessageBus,
    ) -> None:
        """handle(command, uow) dispatches events collected during execution."""

        class OrderCreated(DomainEvent):
            order_id: str

        class Order(AggregateRoot[UUID]):
            item_name: str

        order = Order(id=uuid4(), item_name="widget")
        order._add_event(OrderCreated(order_id=str(order.id)))

        repo: FakeRepository[Order, UUID] = FakeRepository()
        await repo.add(order)
        uow = FakeUnitOfWork(repository=repo)

        dispatched: list[DomainEvent] = []

        async def event_handler(event: OrderCreated) -> None:
            dispatched.append(event)

        async def cmd_handler(cmd: _Cmd) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register_command(_Cmd, cmd_handler)
        bus.register_event(OrderCreated, event_handler)

        result = await bus.handle(_Cmd(data="hello"), uow)

        assert isinstance(result, EmptyCommandResult)
        assert len(dispatched) == 1
        assert isinstance(dispatched[0], OrderCreated)
        assert dispatched[0].order_id == str(order.id)

    @pytest.mark.anyio
    async def test_query_returns_typed_result(self, bus: MessageBus) -> None:
        """query(q) returns typed QueryResult -- no UoW context."""

        async def handler(query: _Qry) -> _QryRes:
            return _QryRes(value=f"queried-{query.data}")

        bus.register_query(_Qry, handler)

        result = await bus.query(_Qry(data="test-value"))

        assert isinstance(result, _QryRes)
        assert result.value == "queried-test-value"

    @pytest.mark.anyio
    async def test_command_handler_exception_propagates(
        self,
        bus: MessageBus,
        uow: FakeUnitOfWork,
    ) -> None:
        """Command handler exception propagates to caller (not swallowed)."""

        async def failing_handler(cmd: _Cmd) -> EmptyCommandResult:
            raise ValueError("command failed")

        bus.register_command(_Cmd, failing_handler)

        with pytest.raises(CommandExecutionError) as exc_info:
            await bus.handle(_Cmd(data="fail"), uow)

        assert isinstance(exc_info.value.__cause__, ValueError)
        assert "command failed" in str(exc_info.value.__cause__)

    @pytest.mark.anyio
    async def test_query_handler_exception_propagates(self, bus: MessageBus) -> None:
        """Query handler exception propagates to caller (not swallowed)."""

        async def failing_handler(query: _Qry) -> _QryRes:
            raise ValueError("query failed")

        bus.register_query(_Qry, failing_handler)

        with pytest.raises(ValueError, match="query failed"):
            await bus.query(_Qry(data="fail"))

    @pytest.mark.anyio
    async def test_command_without_uow_raises_error(self, bus: MessageBus) -> None:
        """handle(command, None) raises ValueError."""

        async def handler(cmd: _Cmd) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register_command(_Cmd, handler)

        with pytest.raises(ValueError, match="UnitOfWork is required"):
            await bus.handle(_Cmd(data="test"), None)

    @pytest.mark.anyio
    async def test_command_with_multiple_events_all_dispatched(
        self,
        bus: MessageBus,
    ) -> None:
        """When a command produces multiple events, all are dispatched."""

        class EventA(DomainEvent):
            pass

        class EventB(DomainEvent):
            pass

        class MultiEventAggregate(AggregateRoot[UUID]):
            pass

        agg = MultiEventAggregate(id=uuid4())
        agg._add_event(EventA())
        agg._add_event(EventB())

        repo: FakeRepository[MultiEventAggregate, UUID] = FakeRepository()
        await repo.add(agg)
        uow = FakeUnitOfWork(repository=repo)

        dispatched: list[str] = []

        async def handler_a(event: EventA) -> None:
            dispatched.append("A")

        async def handler_b(event: EventB) -> None:
            dispatched.append("B")

        async def cmd_handler(cmd: _Cmd) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register_command(_Cmd, cmd_handler)
        bus.register_event(EventA, handler_a)
        bus.register_event(EventB, handler_b)

        await bus.handle(_Cmd(data="multi"), uow)

        assert dispatched == ["A", "B"]

    @pytest.mark.anyio
    async def test_unregistered_command_raises_error(
        self,
        bus: MessageBus,
        uow: FakeUnitOfWork,
    ) -> None:
        """Dispatching an unregistered command raises NoHandlerRegisteredError."""
        with pytest.raises(NoHandlerRegisteredError):
            await bus.handle(_Cmd(data="unknown"), uow)

    @pytest.mark.anyio
    async def test_unregistered_query_raises_error(self, bus: MessageBus) -> None:
        """Dispatching an unregistered query raises NoHandlerRegisteredError."""
        with pytest.raises(NoHandlerRegisteredError):
            await bus.query(_Qry(data="unknown"))


# ════════════════════════════════════════════════════════════════════════════
# DCE-43: Event handler orchestration
# ════════════════════════════════════════════════════════════════════════════


class TestEventOrchestration:
    """Event handler failure isolation, sequencing, and call-local queue."""

    @pytest.mark.anyio
    async def test_event_handler_failure_logged_and_swallowed(
        self,
        bus: MessageBus,
        caplog: Any,
    ) -> None:
        """Event handler failure is logged and swallowed -- remaining handlers run."""
        results: list[str] = []

        async def failing_handler(event: _Evt) -> None:
            results.append("failing")
            raise ValueError("handler failure")

        async def success_handler(event: _Evt) -> None:
            results.append("success")

        async def cmd_handler(cmd: _Cmd) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register_command(_Cmd, cmd_handler)
        bus.register_event(_Evt, failing_handler)
        bus.register_event(_Evt, success_handler)

        caplog.set_level(logging.ERROR, logger="pydomain.message_bus")

        uow = await _store_event(_Evt(data="test"))
        result = await bus.handle(_Cmd(data="test"), uow)

        assert isinstance(result, EmptyCommandResult)
        assert results == ["failing", "success"]

        failing_logs = [
            r
            for r in caplog.records
            if "Event handler" in r.getMessage() and r.levelno == logging.ERROR
        ]
        assert len(failing_logs) == 1

    @pytest.mark.anyio
    async def test_event_handler_failure_does_not_block_other_handlers(
        self,
        bus: MessageBus,
    ) -> None:
        """All remaining handlers execute even when multiple handlers fail."""
        results: list[str] = []

        async def handler_fail_1(event: _Evt) -> None:
            results.append("fail1")
            raise ValueError("first failure")

        async def handler_ok(event: _Evt) -> None:
            results.append("ok")

        async def handler_fail_2(event: _Evt) -> None:
            results.append("fail2")
            raise RuntimeError("second failure")

        async def cmd_handler(cmd: _Cmd) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register_command(_Cmd, cmd_handler)
        bus.register_event(_Evt, handler_fail_1)
        bus.register_event(_Evt, handler_ok)
        bus.register_event(_Evt, handler_fail_2)

        uow = await _store_event(_Evt(data="test"))
        result = await bus.handle(_Cmd(data="test"), uow)

        assert isinstance(result, EmptyCommandResult)
        assert results == ["fail1", "ok", "fail2"]

    @pytest.mark.anyio
    async def test_event_handlers_dispatched_sequentially(
        self,
        bus: MessageBus,
    ) -> None:
        """Event handlers are dispatched sequentially (one at a time)."""
        order: list[str] = []

        async def handler1(event: _Evt) -> None:
            order.append("handler1")

        async def handler2(event: _Evt) -> None:
            order.append("handler2")

        async def cmd_handler(cmd: _Cmd) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register_command(_Cmd, cmd_handler)
        bus.register_event(_Evt, handler1)
        bus.register_event(_Evt, handler2)

        uow = await _store_event(_Evt(data="test"))
        await bus.handle(_Cmd(data="test"), uow)

        assert order == ["handler1", "handler2"]

    @pytest.mark.anyio
    async def test_sequential_events_handled_in_order(
        self,
        bus: MessageBus,
    ) -> None:
        """All handlers for event N finish before handlers for event N+1 start."""

        class EventA(DomainEvent):
            data: str

        class EventB(DomainEvent):
            data: str

        class TwoEventAggregate(AggregateRoot[UUID]):
            pass

        order: list[str] = []

        async def handler_a1(event: EventA) -> None:
            order.append("a1")

        async def handler_a2(event: EventA) -> None:
            order.append("a2")

        async def handler_b(event: EventB) -> None:
            order.append("b")

        agg = TwoEventAggregate(id=uuid4())
        agg._add_event(EventA(data="first"))
        agg._add_event(EventB(data="second"))

        repo: FakeRepository[TwoEventAggregate, UUID] = FakeRepository()
        await repo.add(agg)
        uow = FakeUnitOfWork(repository=repo)

        bus.register_command(_Cmd, self._dummy_cmd_handler)
        bus.register_event(EventA, handler_a1)
        bus.register_event(EventA, handler_a2)
        bus.register_event(EventB, handler_b)

        await bus.handle(_Cmd(data="dispatch"), uow)

        assert order == ["a1", "a2", "b"]

    @staticmethod
    async def _dummy_cmd_handler(cmd: Command[Any]) -> EmptyCommandResult:
        return EmptyCommandResult()

    @pytest.mark.anyio
    async def test_event_queue_is_call_local(self, bus: MessageBus) -> None:
        """New handle() call starts with empty event queue."""
        handler_call_count: list[int] = [0]

        class HasEventsAggregate(AggregateRoot[UUID]):
            pass

        async def event_handler(event: _Evt) -> None:
            handler_call_count[0] += 1

        async def cmd_handler(cmd: _Cmd) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register_command(_Cmd, cmd_handler)
        bus.register_event(_Evt, event_handler)

        # First handle() call: produce a _Evt via the aggregate
        agg1 = HasEventsAggregate(id=uuid4())
        agg1._add_event(_Evt(data="first"))
        repo1: FakeRepository[HasEventsAggregate, UUID] = FakeRepository()
        await repo1.add(agg1)
        uow1 = FakeUnitOfWork(repository=repo1)

        await bus.handle(_Cmd(data="first"), uow1)
        assert handler_call_count[0] == 1

        # Second handle() call: aggregate has no events
        agg2 = HasEventsAggregate(id=uuid4())
        repo2: FakeRepository[HasEventsAggregate, UUID] = FakeRepository()
        await repo2.add(agg2)
        uow2 = FakeUnitOfWork(repository=repo2)

        await bus.handle(_Cmd(data="second"), uow2)
        # Count must still be 1 -- no new events from the second call
        assert handler_call_count[0] == 1

    @pytest.mark.anyio
    async def test_no_event_dispatch_when_command_produces_no_events(
        self,
        bus: MessageBus,
        uow: FakeUnitOfWork,
    ) -> None:
        """When a command produces no domain events, nothing is dispatched."""
        handler_called: list[bool] = [False]

        async def event_handler(event: _Evt) -> None:
            handler_called[0] = True

        async def cmd_handler(cmd: _Cmd) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register_command(_Cmd, cmd_handler)
        bus.register_event(_Evt, event_handler)

        await bus.handle(_Cmd(data="no-events"), uow)

        assert not handler_called[0]

    @pytest.mark.anyio
    async def test_event_handlers_no_uow_parameter(self, bus: MessageBus) -> None:
        """Event handlers are dispatched without a Unit of Work.

        The handler receives only the event -- no UoW is passed by the
        MessageBus (unlike command handlers which execute inside a
        UnitOfWork context).
        """
        handler_called: list[bool] = [False]

        async def event_handler(event: _Evt) -> None:
            handler_called[0] = True

        async def cmd_handler(cmd: _Cmd) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register_command(_Cmd, cmd_handler)
        bus.register_event(_Evt, event_handler)

        uow = await _store_event(_Evt(data="test"))
        await bus.handle(_Cmd(data="test"), uow)

        assert handler_called[0]

    @pytest.mark.anyio
    async def test_events_with_no_handlers_are_ignored(
        self,
        bus: MessageBus,
        uow: FakeUnitOfWork,
    ) -> None:
        """Events with no registered handlers are silently ignored."""

        class UnhandledEvent(DomainEvent):
            pass

        async def cmd_handler(cmd: _Cmd) -> EmptyCommandResult:
            return EmptyCommandResult()

        class AggregateWithEvent(AggregateRoot[UUID]):
            pass

        agg = AggregateWithEvent(id=uuid4())
        agg._add_event(UnhandledEvent())

        repo: FakeRepository[AggregateWithEvent, UUID] = FakeRepository()
        await repo.add(agg)
        uow_with_event = FakeUnitOfWork(repository=repo)

        bus.register_command(_Cmd, cmd_handler)

        # Should not raise -- event with no handler is ignored
        result = await bus.handle(_Cmd(data="unhandled"), uow_with_event)

        assert isinstance(result, EmptyCommandResult)

    @pytest.mark.anyio
    async def test_event_handler_called_with_correct_event_data(
        self,
        bus: MessageBus,
    ) -> None:
        """Event handler receives the correct event instance with all fields."""
        received: list[_Evt] = []

        async def event_handler(event: _Evt) -> None:
            received.append(event)

        async def cmd_handler(cmd: _Cmd) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register_command(_Cmd, cmd_handler)
        bus.register_event(_Evt, event_handler)

        test_event = _Evt(data="specific-data")
        uow = await _store_event(test_event)
        await bus.handle(_Cmd(data="test"), uow)

        assert len(received) == 1
        assert received[0].data == "specific-data"

    @pytest.mark.anyio
    async def test_event_handler_with_bus_injection_dispatches_command(
        self,
        bus: MessageBus,
    ) -> None:
        """EventHandler can receive the bus via constructor to dispatch commands.

        This demonstrates the orchestration pattern: an event handler
        receives the MessageBus through its constructor and dispatches
        a new command in response to the event.
        """
        commands_dispatched: list[str] = []

        class TriggerCommand(Command[EmptyCommandResult]):
            payload: str

        class TriggerEvent(DomainEvent):
            payload: str

        class OrchestratingHandler:
            """Receives bus via constructor, dispatches command on event."""

            def __init__(self, bus: MessageBus) -> None:
                self._bus = bus

            async def __call__(self, event: TriggerEvent) -> None:
                # Dispatch a new command in response to the event
                await self._bus.handle(
                    TriggerCommand(payload=event.payload),
                    FakeUnitOfWork(repository=FakeRepository()),
                )

        async def trigger_cmd_handler(
            cmd: TriggerCommand,
        ) -> EmptyCommandResult:
            commands_dispatched.append(cmd.payload)
            return EmptyCommandResult()

        bus.register_command(TriggerCommand, trigger_cmd_handler)

        async def dummy_cmd_handler(cmd: _Cmd) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register_command(_Cmd, dummy_cmd_handler)
        bus.register_event(
            TriggerEvent,
            OrchestratingHandler(bus=bus),
        )

        # Trigger the orchestration by producing an event
        class TriggerAggregate(AggregateRoot[UUID]):
            pass

        agg = TriggerAggregate(id=uuid4())
        agg._add_event(TriggerEvent(payload="orchestrated"))
        repo: FakeRepository[TriggerAggregate, UUID] = FakeRepository()
        await repo.add(agg)
        uow = FakeUnitOfWork(repository=repo)

        await bus.handle(_Cmd(data="start"), uow)

        assert commands_dispatched == ["orchestrated"]
