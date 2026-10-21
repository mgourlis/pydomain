from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from pydomain.cqrs import (
    Command,
    CommandBus,
    CommandExecutionError,
    CommandResult,
    EmptyCommandResult,
    HandlerAlreadyRegisteredError,
    NoHandlerRegisteredError,
    UnitOfWorkAlreadyCommittedError,
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
from tests.support.fault_injection import FaultInjector


class TestRegister:
    @pytest.mark.anyio
    async def test_register_handler(self, bus: CommandBus) -> None:
        async def handler(cmd: MakeGreeting, uow: Any = None) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register(MakeGreeting, handler, uow_factory=lambda: FakeUnitOfWork())

    @pytest.mark.anyio
    async def test_duplicate_registration_raises_error(self, bus: CommandBus) -> None:
        async def handler1(cmd: MakeGreeting, uow: Any = None) -> EmptyCommandResult:
            return EmptyCommandResult()

        async def handler2(cmd: MakeGreeting, uow: Any = None) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register(MakeGreeting, handler1, uow_factory=lambda: FakeUnitOfWork())
        with pytest.raises(HandlerAlreadyRegisteredError):
            bus.register(MakeGreeting, handler2, uow_factory=lambda: FakeUnitOfWork())


class TestDispatch:
    @pytest.mark.anyio
    async def test_dispatch_void_command(
        self,
        bus: CommandBus,
        uow: FakeUnitOfWork,
        make_greeting_handler: Any,
    ) -> None:
        bus.register(MakeGreeting, make_greeting_handler, uow_factory=lambda: uow)
        cmd = MakeGreeting(name="World")

        result, events = await bus.dispatch(cmd)

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
        bus.register(GreetPerson, greet_person_handler, uow_factory=lambda: uow)
        cmd = GreetPerson(name="Alice")

        result, events = await bus.dispatch(cmd)

        assert isinstance(result, GreetingResult)
        assert result.greeting == "Hello, Alice!"
        assert uow._committed

    @pytest.mark.anyio
    async def test_unregistered_command_raises_error(
        self,
        bus: CommandBus,
    ) -> None:
        cmd = MakeGreeting(name="World")

        with pytest.raises(NoHandlerRegisteredError):
            await bus.dispatch(cmd)

    @pytest.mark.anyio
    async def test_handler_exception_propagates(
        self,
        bus: CommandBus,
        uow: FakeUnitOfWork,
    ) -> None:
        async def failing_handler(
            cmd: MakeGreeting, uow: Any = None
        ) -> EmptyCommandResult:
            msg = "handler failed"
            raise ValueError(msg)

        bus.register(MakeGreeting, failing_handler, uow_factory=lambda: uow)
        cmd = MakeGreeting(name="World")

        with pytest.raises(CommandExecutionError) as exc_info:
            await bus.dispatch(cmd)

        assert isinstance(exc_info.value.__cause__, ValueError)
        assert str(exc_info.value.__cause__) == "handler failed"
        assert "MakeGreeting" in str(exc_info.value)
        assert exc_info.value.command is cmd

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

        async def handler(cmd: CountThings, uow: Any = None) -> CountingResult:
            trace.append("handler")
            return CountingResult(count=len(cmd.values))

        bus.register(
            CountThings,
            handler,
            uow_factory=lambda: uow,
            behaviors=[OuterBehavior(), InnerBehavior()],
        )

        cmd = CountThings(values=[1, 2, 3])
        result, events = await bus.dispatch(cmd)

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
        await repo.save(order)

        uow = FakeUnitOfWork(repository=repo)

        class MyResult(CommandResult):
            success: bool

        class MyCommand(Command[MyResult]):
            data: str

        async def handler(cmd: MyCommand, uow: Any = None) -> MyResult:
            return MyResult(success=True)

        bus.register(MyCommand, handler, uow_factory=lambda: uow)
        cmd = MyCommand(data="hello")

        result, events = await bus.dispatch(cmd)

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

        async def handler(cmd: MakeGreeting, uow: Any = None) -> EmptyCommandResult:
            return None  # type: ignore[return-value]  # Runtime protocol violation

        bus.register(MakeGreeting, handler, uow_factory=lambda: uow)
        cmd = MakeGreeting(name="World")

        result, events = await bus.dispatch(cmd)

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
        bus.register(
            MakeGreeting, make_greeting_handler, uow_factory=lambda: uow, behaviors=[]
        )
        cmd = MakeGreeting(name="World")

        result, events = await bus.dispatch(cmd)

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

        async def handler(cmd: GreetPerson, uow: Any = None) -> GreetingResult:
            nonlocal handler_called
            handler_called = True
            return GreetingResult(greeting="from handler")

        bus.register(
            GreetPerson,
            handler,
            uow_factory=lambda: uow,
            behaviors=[ShortCircuitBehavior()],
        )
        cmd = GreetPerson(name="Alice")

        result, events = await bus.dispatch(cmd)

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

        bus.register(
            MakeGreeting,
            make_greeting_handler,
            uow_factory=lambda: uow,
            behaviors=[FailingBehavior()],
        )
        cmd = MakeGreeting(name="World")

        with pytest.raises(CommandExecutionError) as exc_info:
            await bus.dispatch(cmd)

        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert str(exc_info.value.__cause__) == "behavior failed"

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
        await repo.save(order)

        uow = FakeUnitOfWork(repository=repo)

        class MyResult(CommandResult):
            success: bool

        class MyCommand(Command[MyResult]):
            data: str

        async def handler(cmd: MyCommand, uow: Any = None) -> MyResult:
            return MyResult(success=True)

        bus.register(MyCommand, handler, uow_factory=lambda: uow)
        cmd = MyCommand(data="test")

        result, events = await bus.dispatch(cmd)

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
        await repo.save(order)

        uow = FakeUnitOfWork(repository=repo)

        class ShipOrderResult(CommandResult):
            shipped: bool

        class ShipOrder(Command[ShipOrderResult]):
            order_id: UUID

        async def handler(cmd: ShipOrder, uow: Any = None) -> ShipOrderResult:
            aggregate = await repo.get_by_id(cmd.order_id)
            assert aggregate is not None
            aggregate.mark_shipped()
            await repo.save(aggregate)
            return ShipOrderResult(shipped=aggregate.shipped)

        bus.register(ShipOrder, handler, uow_factory=lambda: uow)
        cmd = ShipOrder(order_id=order.id)

        result, events = await bus.dispatch(cmd)

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


# ═══════════════════════════════════════════════════════════════════════════
# DCE-116 (ADR-068): a failed dispatch rolls back once and drains the buffers
# ═══════════════════════════════════════════════════════════════════════════


class _InvoiceIssued(DomainEvent):
    invoice_id: UUID


class _Invoice(AggregateRoot[UUID]):
    @classmethod
    def issue(cls, invoice_id: UUID) -> _Invoice:
        invoice = cls(id=invoice_id)
        invoice._add_event(_InvoiceIssued(invoice_id=invoice_id))
        return invoice


class _IssueInvoice(Command[EmptyCommandResult]):
    invoice_id: UUID
    refuse: bool = False


class _InvoiceRefused(Exception):
    """Raised by the handler after it saved, to simulate a business refusal."""


class _RollbackFailed(Exception):
    """Raised by the storage session's rollback."""


class _InvoiceUoW(FakeUnitOfWork):
    """A FakeUnitOfWork with a typed ``invoices`` repository."""

    def __init__(self, invoices: FakeRepository[_Invoice, UUID] | None = None) -> None:
        self.invoices = invoices if invoices is not None else FakeRepository()
        super().__init__(repositories={"invoices": self.invoices})


async def _issue_invoice(cmd: _IssueInvoice, uow: _InvoiceUoW) -> EmptyCommandResult:
    await uow.invoices.save(_Invoice.issue(cmd.invoice_id))
    if cmd.refuse:
        raise _InvoiceRefused("synthetic refusal")
    return EmptyCommandResult()


class _CountingUoW(_InvoiceUoW):
    """Counts the calls to ``rollback()``; can make ``_commit`` fail."""

    def __init__(self, *, fail_commit: bool = False) -> None:
        super().__init__()
        self.rollbacks = 0
        self.fail_commit = fail_commit

    async def _commit(self) -> None:
        if self.fail_commit:
            raise RuntimeError("synthetic storage failure")

    async def rollback(self) -> None:
        self.rollbacks += 1
        await super().rollback()


class _Session:
    """Storage session stand-in whose ``rollback`` a FaultInjector can break."""

    async def rollback(self) -> None:
        return None


class _SessionUoW(_InvoiceUoW):
    """Rolls back its session, then the base class."""

    def __init__(self, session: _Session) -> None:
        super().__init__()
        self.session = session

    async def rollback(self) -> None:
        await self.session.rollback()
        await super().rollback()


def _invoice_ids(events: list[DomainEvent]) -> set[UUID]:
    return {e.invoice_id for e in events if isinstance(e, _InvoiceIssued)}


class TestFailedDispatch:
    @pytest.mark.anyio
    async def test_refused_command_event_never_reaches_next_commit(
        self, bus: CommandBus
    ) -> None:
        shared: FakeRepository[_Invoice, UUID] = FakeRepository()
        bus.register(
            _IssueInvoice, _issue_invoice, uow_factory=lambda: _InvoiceUoW(shared)
        )
        refused = _IssueInvoice(invoice_id=uuid4(), refuse=True)
        accepted = _IssueInvoice(invoice_id=uuid4())

        with pytest.raises(CommandExecutionError):
            await bus.dispatch(refused)
        _, events = await bus.dispatch(accepted)

        assert _invoice_ids(events) == {accepted.invoice_id}
        assert all(e.correlation_id == accepted.command_id for e in events)

    @pytest.mark.parametrize("fail_commit", [False, True], ids=["handler", "commit"])
    @pytest.mark.anyio
    async def test_rollback_runs_once_per_failed_dispatch(
        self, bus: CommandBus, fail_commit: bool
    ) -> None:
        uow = _CountingUoW(fail_commit=fail_commit)
        bus.register(_IssueInvoice, _issue_invoice, uow_factory=lambda: uow)
        command = _IssueInvoice(invoice_id=uuid4(), refuse=not fail_commit)

        with pytest.raises(CommandExecutionError):
            await bus.dispatch(command)

        assert uow.rollbacks == 1

    @pytest.mark.anyio
    async def test_raising_rollback_keeps_command_execution_error(
        self, bus: CommandBus
    ) -> None:
        fault = FaultInjector(
            _Session(), at=1, raises=_RollbackFailed, methods={"rollback"}
        )
        bus.register(
            _IssueInvoice,
            _issue_invoice,
            uow_factory=lambda: _SessionUoW(fault.proxy),
        )
        command = _IssueInvoice(invoice_id=uuid4(), refuse=True)

        with pytest.raises(CommandExecutionError) as raised:
            await bus.dispatch(command)

        assert raised.value.command is command
        assert isinstance(raised.value.__cause__, _InvoiceRefused)
        assert isinstance(raised.value.__context__, _RollbackFailed)
        assert fault.awaited == ["rollback"]

    @pytest.mark.anyio
    async def test_returned_events_survive_a_later_dispatch_on_the_same_uow(
        self, bus: CommandBus
    ) -> None:
        shared_uow = _InvoiceUoW()  # one instance for every dispatch, on purpose
        bus.register(_IssueInvoice, _issue_invoice, uow_factory=lambda: shared_uow)
        first = _IssueInvoice(invoice_id=uuid4())

        _, events = await bus.dispatch(first)
        await bus.dispatch(_IssueInvoice(invoice_id=uuid4()))

        assert _invoice_ids(events) == {first.invoice_id}

    @pytest.mark.anyio
    async def test_handler_that_commits_gets_command_execution_error(
        self, bus: CommandBus
    ) -> None:
        async def committing_handler(
            cmd: _IssueInvoice, uow: _InvoiceUoW
        ) -> EmptyCommandResult:
            await _issue_invoice(cmd, uow)
            await uow.commit()  # against ADR-020: the bus owns the commit
            return EmptyCommandResult()

        bus.register(_IssueInvoice, committing_handler, uow_factory=_InvoiceUoW)

        with pytest.raises(CommandExecutionError) as raised:
            await bus.dispatch(_IssueInvoice(invoice_id=uuid4()))

        assert isinstance(raised.value.__cause__, UnitOfWorkAlreadyCommittedError)

    @pytest.mark.anyio
    async def test_failure_entering_the_uow_propagates_unwrapped(
        self, bus: CommandBus
    ) -> None:
        """Only the pipeline and the commit are wrapped; entering is not."""

        class _EntryFailed(Exception):
            pass

        class _UnreachableUoW(_InvoiceUoW):
            async def __aenter__(self) -> _InvoiceUoW:
                raise _EntryFailed

        bus.register(_IssueInvoice, _issue_invoice, uow_factory=_UnreachableUoW)

        with pytest.raises(_EntryFailed):
            await bus.dispatch(_IssueInvoice(invoice_id=uuid4()))
