from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from pydomain.cqrs import (
    Command,
    CommandBus,
    CommandResult,
    EmptyCommandResult,
    HandlerAlreadyRegisteredError,
    NoHandlerRegisteredError,
)
from pydomain.cqrs.behaviors import MessageContext, NextHandler
from pydomain.cqrs.unit_of_work import UnitOfWork
from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing import FakeRepository, FakeUnitOfWork
from tests.cqrs.conftest import (
    CountingResult,
    CountThings,
    GreetingResult,
    GreetPerson,
    MakeGreeting,
)


class TestRegister:
    @pytest.mark.anyio
    async def test_register_handler(self, bus: CommandBus) -> None:
        async def handler(cmd: MakeGreeting) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register(MakeGreeting, handler)

    @pytest.mark.anyio
    async def test_duplicate_registration_raises_error(self, bus: CommandBus) -> None:
        async def handler1(cmd: MakeGreeting) -> EmptyCommandResult:
            return EmptyCommandResult()

        async def handler2(cmd: MakeGreeting) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register(MakeGreeting, handler1)
        with pytest.raises(HandlerAlreadyRegisteredError):
            bus.register(MakeGreeting, handler2)


class TestDispatch:
    @pytest.mark.anyio
    async def test_dispatch_void_command(
        self,
        bus: CommandBus,
        uow: FakeUnitOfWork,
        make_greeting_handler: Any,
    ) -> None:
        bus.register(MakeGreeting, make_greeting_handler)
        cmd = MakeGreeting(name="World")

        result, events = await bus.dispatch(cmd, uow)

        assert isinstance(result, EmptyCommandResult)
        assert events == []
        assert uow._committed
        assert not uow._rolled_back

    @pytest.mark.anyio
    async def test_dispatch_with_result(
        self,
        bus: CommandBus,
        uow: FakeUnitOfWork,
        greet_person_handler: Any,
    ) -> None:
        bus.register(GreetPerson, greet_person_handler)
        cmd = GreetPerson(name="Alice")

        result, events = await bus.dispatch(cmd, uow)

        assert isinstance(result, GreetingResult)
        assert result.greeting == "Hello, Alice!"
        assert uow._committed

    @pytest.mark.anyio
    async def test_unregistered_command_raises_error(
        self,
        bus: CommandBus,
        uow: FakeUnitOfWork,
    ) -> None:
        cmd = MakeGreeting(name="World")

        with pytest.raises(NoHandlerRegisteredError):
            await bus.dispatch(cmd, uow)

    @pytest.mark.anyio
    async def test_handler_exception_propagates(
        self,
        bus: CommandBus,
        uow: FakeUnitOfWork,
    ) -> None:
        async def failing_handler(cmd: MakeGreeting) -> EmptyCommandResult:
            msg = "handler failed"
            raise ValueError(msg)

        bus.register(MakeGreeting, failing_handler)
        cmd = MakeGreeting(name="World")

        with pytest.raises(ValueError, match="handler failed"):
            await bus.dispatch(cmd, uow)

        assert uow._rolled_back
        assert not uow._committed

    @pytest.mark.anyio
    async def test_dispatch_with_pipeline_behaviors(
        self,
        bus: CommandBus,
        uow: FakeUnitOfWork,
    ) -> None:
        trace: list[str] = []

        class OuterBehavior:
            async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
                trace.append("outer_before")
                result = await next()
                trace.append("outer_after")
                return result

        class InnerBehavior:
            async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
                trace.append("inner_before")
                result = await next()
                trace.append("inner_after")
                return result

        async def handler(cmd: CountThings) -> CountingResult:
            trace.append("handler")
            return CountingResult(count=len(cmd.values))

        bus.register(
            CountThings,
            handler,
            behaviors=[OuterBehavior(), InnerBehavior()],
        )

        cmd = CountThings(values=[1, 2, 3])
        result, events = await bus.dispatch(cmd, uow)

        assert isinstance(result, CountingResult)
        assert result.count == 3
        assert trace == [
            "outer_before",
            "inner_before",
            "handler",
            "inner_after",
            "outer_after",
        ]
        assert uow._committed

    @pytest.mark.anyio
    async def test_events_are_stamped_with_tracing_ids(
        self,
        bus: CommandBus,
    ) -> None:
        """Collected events have correlation_id and causation_id set after dispatch."""

        class OrderPlaced(DomainEvent):
            order_id: str

        class Order(AggregateRoot[UUID]):
            item_name: str

        order = Order(id=uuid4(), item_name="widget")
        order._add_event(OrderPlaced(order_id=str(order.id)))

        repo: FakeRepository[Order, UUID] = FakeRepository()
        await repo.add(order)

        uow = FakeUnitOfWork(repository=repo)

        class MyResult(CommandResult):
            success: bool

        class MyCommand(Command[MyResult]):
            data: str

        async def handler(cmd: MyCommand) -> MyResult:
            return MyResult(success=True)

        bus.register(MyCommand, handler)
        cmd = MyCommand(data="hello")

        result, events = await bus.dispatch(cmd, uow)

        assert isinstance(result, MyResult)
        assert result.success
        assert len(events) == 1
        event = events[0]
        assert isinstance(event, OrderPlaced)
        assert event.correlation_id is not None
        assert event.causation_id is not None
        assert event.correlation_id == cmd.command_id
        assert event.causation_id == cmd.command_id

    @pytest.mark.anyio
    async def test_handler_returning_none_wraps_in_empty_result(
        self,
        bus: CommandBus,
        uow: FakeUnitOfWork,
    ) -> None:
        """Handler returning None is wrapped in EmptyCommandResult by the bus."""

        async def handler(cmd: MakeGreeting) -> EmptyCommandResult:
            return None  # type: ignore[return-value]  # Runtime protocol violation

        bus.register(MakeGreeting, handler)
        cmd = MakeGreeting(name="World")

        result, events = await bus.dispatch(cmd, uow)

        assert isinstance(result, EmptyCommandResult)
        assert uow._committed
        assert not uow._rolled_back


class TestPipelineBehaviors:
    """Pipeline behavior edge cases: empty list, short-circuit, exception."""

    @pytest.mark.anyio
    async def test_empty_behavior_list(
        self,
        bus: CommandBus,
        uow: FakeUnitOfWork,
        make_greeting_handler: Any,
    ) -> None:
        """An empty behavior list still dispatches correctly (no wrappers)."""
        bus.register(MakeGreeting, make_greeting_handler, behaviors=[])
        cmd = MakeGreeting(name="World")

        result, events = await bus.dispatch(cmd, uow)

        assert isinstance(result, EmptyCommandResult)
        assert uow._committed
        assert not uow._rolled_back

    @pytest.mark.anyio
    async def test_behavior_short_circuits(
        self,
        bus: CommandBus,
        uow: FakeUnitOfWork,
    ) -> None:
        """A behavior that does not call next() prevents the handler from executing."""
        handler_called: bool = False

        class ShortCircuitBehavior:
            async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
                return GreetingResult(greeting="short-circuited")

        async def handler(cmd: GreetPerson) -> GreetingResult:
            nonlocal handler_called
            handler_called = True
            return GreetingResult(greeting="from handler")

        bus.register(GreetPerson, handler, behaviors=[ShortCircuitBehavior()])
        cmd = GreetPerson(name="Alice")

        result, events = await bus.dispatch(cmd, uow)

        assert isinstance(result, GreetingResult)
        assert result.greeting == "short-circuited"
        assert not handler_called
        assert uow._committed

    @pytest.mark.anyio
    async def test_behavior_exception_propagates(
        self,
        bus: CommandBus,
        uow: FakeUnitOfWork,
        make_greeting_handler: Any,
    ) -> None:
        """An exception raised in a pipeline behavior triggers rollback."""

        class FailingBehavior:
            async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
                msg = "behavior failed"
                raise RuntimeError(msg)

        bus.register(MakeGreeting, make_greeting_handler, behaviors=[FailingBehavior()])
        cmd = MakeGreeting(name="World")

        with pytest.raises(RuntimeError, match="behavior failed"):
            await bus.dispatch(cmd, uow)

        assert uow._rolled_back
        assert not uow._committed


class TestEventCollection:
    """Domain event collection through the Unit of Work and repository."""

    @pytest.mark.anyio
    async def test_event_collection_through_repository(
        self,
        bus: CommandBus,
    ) -> None:
        """Events from repository aggregates are collected and stamped on dispatch."""

        class OrderCreated(DomainEvent):
            order_id: str

        class Order(AggregateRoot[UUID]):
            item_name: str

        order = Order(id=uuid4(), item_name="widget")
        order._add_event(OrderCreated(order_id=str(order.id)))

        repo: FakeRepository[Order, UUID] = FakeRepository()
        await repo.add(order)

        uow = FakeUnitOfWork(repository=repo)

        class MyResult(CommandResult):
            success: bool

        class MyCommand(Command[MyResult]):
            data: str

        async def handler(cmd: MyCommand) -> MyResult:
            return MyResult(success=True)

        bus.register(MyCommand, handler)
        cmd = MyCommand(data="test")

        result, events = await bus.dispatch(cmd, uow)

        assert isinstance(result, MyResult)
        assert result.success
        assert len(events) == 1
        event = events[0]
        assert isinstance(event, OrderCreated)
        assert event.order_id == str(order.id)
        assert event.correlation_id is not None
        assert event.causation_id is not None
        assert event.correlation_id == cmd.command_id
        assert event.causation_id == cmd.command_id

    @pytest.mark.anyio
    async def test_handler_modifies_aggregate_events_collected(
        self,
        bus: CommandBus,
    ) -> None:
        """Handler loads aggregate, modifies it, UoW collects stamped events."""

        class OrderShipped(DomainEvent):
            order_id: str

        class Order(AggregateRoot[UUID]):
            item_name: str
            shipped: bool = False

            def mark_shipped(self) -> None:
                self.shipped = True
                self._add_event(OrderShipped(order_id=str(self.id)))

        order = Order(id=uuid4(), item_name="widget")

        repo: FakeRepository[Order, UUID] = FakeRepository()
        await repo.add(order)

        uow = FakeUnitOfWork(repository=repo)

        class ShipOrderResult(CommandResult):
            shipped: bool

        class ShipOrder(Command[ShipOrderResult]):
            order_id: UUID

        async def handler(cmd: ShipOrder) -> ShipOrderResult:
            aggregate = await repo.get_by_id(cmd.order_id)
            assert aggregate is not None
            aggregate.mark_shipped()
            return ShipOrderResult(shipped=aggregate.shipped)

        bus.register(ShipOrder, handler)
        cmd = ShipOrder(order_id=order.id)

        result, events = await bus.dispatch(cmd, uow)

        assert isinstance(result, ShipOrderResult)
        assert result.shipped is True
        assert len(events) == 1
        event = events[0]
        assert isinstance(event, OrderShipped)
        assert event.order_id == str(order.id)
        assert event.correlation_id == cmd.command_id
        assert event.causation_id == cmd.command_id

    @pytest.mark.anyio
    async def test_fake_uow_conforms_to_unit_of_work_protocol(
        self,
    ) -> None:
        """FakeUnitOfWork satisfies the UnitOfWork Protocol at runtime."""
        uow = FakeUnitOfWork()
        assert isinstance(uow, UnitOfWork)
