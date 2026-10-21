from __future__ import annotations

import asyncio
import traceback
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
from pydomain.cqrs.behaviors import (
    AggregateLockingBehavior,
    MessageContext,
    MessageKind,
    NextHandler,
)
from pydomain.cqrs.locking import DictLockKeyResolver
from pydomain.cqrs.unit_of_work import UnitOfWork
from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing import FakeRepository, FakeUnitOfWork
from pydomain.testing.fake_lock_provider import FakeLockProvider
from tests.cqrs.conftest import (
    CountingResult,
    CountThings,
    GreetingResult,
    GreetPerson,
    MakeGreeting,
)
from tests.support.fault_injection import FaultInjector
from tests.support.recording_unit_of_work import RecordingUnitOfWork


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
        uow: RecordingUnitOfWork,
        make_greeting_handler: Any,
    ) -> None:
        bus.register(MakeGreeting, make_greeting_handler, uow_factory=lambda: uow)
        cmd = MakeGreeting(name="World")

        result, events = await bus.dispatch(cmd)

        assert isinstance(result, EmptyCommandResult)
        assert events == []
        assert uow.hooks == ["commit"]

    @pytest.mark.anyio
    async def test_dispatch_with_result(
        self,
        bus: CommandBus,
        uow: RecordingUnitOfWork,
        greet_person_handler: Any,
    ) -> None:
        bus.register(GreetPerson, greet_person_handler, uow_factory=lambda: uow)
        cmd = GreetPerson(name="Alice")

        result, events = await bus.dispatch(cmd)

        assert isinstance(result, GreetingResult)
        assert result.greeting == "Hello, Alice!"
        assert uow.hooks == ["commit"]

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
        uow: RecordingUnitOfWork,
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

        assert uow.hooks == ["rollback"]

    @pytest.mark.anyio
    async def test_dispatch_with_pipeline_behaviors(
        self,
        bus: CommandBus,
        uow: RecordingUnitOfWork,
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
        assert uow.hooks == ["commit"]

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
        uow: RecordingUnitOfWork,
    ) -> None:
        """Handler returning None is wrapped in EmptyCommandResult by the bus."""

        async def handler(cmd: MakeGreeting, uow: Any = None) -> EmptyCommandResult:
            return None  # type: ignore[return-value]  # Runtime protocol violation

        bus.register(MakeGreeting, handler, uow_factory=lambda: uow)
        cmd = MakeGreeting(name="World")

        result, events = await bus.dispatch(cmd)

        assert isinstance(result, EmptyCommandResult)
        assert uow.hooks == ["commit"]


class TestPipelineBehaviors:
    """Pipeline behavior edge cases: empty list, short-circuit, exception."""

    @pytest.mark.anyio
    async def test_empty_behavior_list(
        self,
        bus: CommandBus,
        uow: RecordingUnitOfWork,
        make_greeting_handler: Any,
    ) -> None:
        """An empty behavior list still dispatches correctly (no wrappers)."""
        bus.register(
            MakeGreeting, make_greeting_handler, uow_factory=lambda: uow, behaviors=[]
        )
        cmd = MakeGreeting(name="World")

        result, events = await bus.dispatch(cmd)

        assert isinstance(result, EmptyCommandResult)
        assert uow.hooks == ["commit"]

    @pytest.mark.anyio
    async def test_behavior_short_circuits(
        self,
        bus: CommandBus,
        uow: RecordingUnitOfWork,
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
        assert uow.hooks == ["commit"]

    @pytest.mark.anyio
    async def test_behavior_exception_propagates(
        self,
        bus: CommandBus,
        uow: RecordingUnitOfWork,
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

        assert uow.hooks == ["rollback"]


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
    """Counts commits and ``rollback()`` calls; can make ``_commit`` fail."""

    def __init__(self, *, fail_commit: bool = False) -> None:
        super().__init__()
        self.commits = 0
        self.rollbacks = 0
        self.fail_commit = fail_commit

    async def _commit(self) -> None:
        if self.fail_commit:
            raise RuntimeError("synthetic storage failure")
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1
        await super().rollback()


class _Session:
    """Storage session stand-in whose ``rollback`` a FaultInjector can break."""

    async def rollback(self) -> None:
        return None


class _SessionUoW(_InvoiceUoW):
    """Rolls back its session in the ``_rollback()`` hook (ADR-068)."""

    def __init__(
        self,
        session: _Session,
        invoices: FakeRepository[_Invoice, UUID] | None = None,
    ) -> None:
        super().__init__(invoices)
        self.session = session

    async def _rollback(self) -> None:
        await self.session.rollback()


class _FailingRollbackUoW(_InvoiceUoW):
    """A unit of work whose storage rollback always raises."""

    async def _rollback(self) -> None:
        raise _RollbackFailed("synthetic-rollback-detail")


async def _sleeping_handler(cmd: _IssueInvoice, uow: _InvoiceUoW) -> EmptyCommandResult:
    await asyncio.sleep(10)
    return EmptyCommandResult()


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
        shared: FakeRepository[_Invoice, UUID] = FakeRepository()
        fault = FaultInjector(
            _Session(), at=1, raises=_RollbackFailed, methods={"rollback"}
        )
        bus.register(
            _IssueInvoice,
            _issue_invoice,
            uow_factory=lambda: _SessionUoW(fault.proxy, shared),
        )
        command = _IssueInvoice(invoice_id=uuid4(), refuse=True)

        with pytest.raises(CommandExecutionError) as raised:
            await bus.dispatch(command)

        assert raised.value.command is command
        assert isinstance(raised.value.__cause__, _InvoiceRefused)
        assert isinstance(raised.value.__context__, _RollbackFailed)
        assert fault.awaited == ["rollback"]

        accepted = _IssueInvoice(invoice_id=uuid4())
        _, events = await bus.dispatch(accepted)
        assert _invoice_ids(events) == {accepted.invoice_id}

    @pytest.mark.anyio
    async def test_rollback_failure_is_noted_by_type_only(
        self, bus: CommandBus
    ) -> None:
        fault = FaultInjector(
            _Session(),
            at=1,
            raises=_RollbackFailed("synthetic-rollback-detail"),
            methods={"rollback"},
        )
        bus.register(
            _IssueInvoice,
            _issue_invoice,
            uow_factory=lambda: _SessionUoW(fault.proxy),
        )

        with pytest.raises(CommandExecutionError) as raised:
            await bus.dispatch(_IssueInvoice(invoice_id=uuid4(), refuse=True))

        assert getattr(raised.value, "__notes__", []) == [
            "clean-up also failed: _RollbackFailed"
        ]
        shown = "".join(traceback.format_exception(raised.value))
        assert "clean-up also failed: _RollbackFailed" in shown
        assert "synthetic-rollback-detail" not in shown

    @pytest.mark.anyio
    async def test_committed_exit_drain_failure_after_handler_commit_is_noted(
        self, bus: CommandBus
    ) -> None:
        """A handler commits, the bus's commit raises, then the exit drain raises."""

        class _BrokenRepo:
            def pull_events(self) -> list[DomainEvent]:
                raise _RollbackFailed("synthetic-drain-detail")

        async def committing_handler(
            cmd: _IssueInvoice, uow: _InvoiceUoW
        ) -> EmptyCommandResult:
            await _issue_invoice(cmd, uow)
            await uow.commit()  # against ADR-020: the bus owns the commit
            uow._repos["broken"] = _BrokenRepo()
            return EmptyCommandResult()

        bus.register(_IssueInvoice, committing_handler, uow_factory=_InvoiceUoW)

        with pytest.raises(CommandExecutionError) as raised:
            await bus.dispatch(_IssueInvoice(invoice_id=uuid4()))

        assert isinstance(raised.value.__cause__, UnitOfWorkAlreadyCommittedError)
        assert getattr(raised.value, "__notes__", []) == [
            "clean-up also failed: _RollbackFailed"
        ]
        assert "synthetic-drain-detail" not in "".join(
            traceback.format_exception(raised.value)
        )

    @pytest.mark.anyio
    async def test_failed_dispatch_without_rollback_failure_has_no_note(
        self, bus: CommandBus
    ) -> None:
        bus.register(_IssueInvoice, _issue_invoice, uow_factory=_InvoiceUoW)

        with pytest.raises(CommandExecutionError) as raised:
            await bus.dispatch(_IssueInvoice(invoice_id=uuid4(), refuse=True))

        assert getattr(raised.value, "__notes__", []) == []

    @pytest.mark.anyio
    async def test_cancelled_dispatch_rolls_back_once_and_is_not_wrapped(
        self, bus: CommandBus
    ) -> None:
        uow = _CountingUoW()

        async def cancelled_handler(
            cmd: _IssueInvoice, uow: _InvoiceUoW
        ) -> EmptyCommandResult:
            await uow.invoices.save(_Invoice.issue(cmd.invoice_id))
            raise asyncio.CancelledError

        bus.register(_IssueInvoice, cancelled_handler, uow_factory=lambda: uow)

        with pytest.raises(asyncio.CancelledError):
            await bus.dispatch(_IssueInvoice(invoice_id=uuid4()))

        assert uow.rollbacks == 1
        assert uow.invoices.pull_events() == []

    @pytest.mark.anyio
    async def test_cancelled_dispatch_with_raising_rollback_propagates_cancellation(
        self, bus: CommandBus
    ) -> None:
        fault = FaultInjector(
            _Session(),
            at=1,
            raises=_RollbackFailed("synthetic-rollback-detail"),
            methods={"rollback"},
        )

        async def cancelled_handler(
            cmd: _IssueInvoice, uow: _InvoiceUoW
        ) -> EmptyCommandResult:
            await uow.invoices.save(_Invoice.issue(cmd.invoice_id))
            raise asyncio.CancelledError

        shared: FakeRepository[_Invoice, UUID] = FakeRepository()
        bus.register(
            _IssueInvoice,
            cancelled_handler,
            uow_factory=lambda: _SessionUoW(fault.proxy, shared),
        )

        with pytest.raises(asyncio.CancelledError) as raised:
            await bus.dispatch(_IssueInvoice(invoice_id=uuid4()))

        assert fault.awaited == ["rollback"]
        assert raised.value.__notes__ == ["clean-up also failed: _RollbackFailed"]
        assert isinstance(raised.value.__context__, _RollbackFailed)
        assert shared.pull_events() == []
        shown = "".join(traceback.format_exception(raised.value))
        assert "clean-up also failed: _RollbackFailed" in shown
        assert "synthetic-rollback-detail" not in shown

    @pytest.mark.anyio
    async def test_suppressing_uow_exit_still_raises_command_execution_error(
        self, bus: CommandBus
    ) -> None:
        class _SuppressingUoW(_InvoiceUoW):
            async def __aexit__(self, *exc_info: Any) -> bool:  # type: ignore[override]
                await super().__aexit__(*exc_info)
                return True

        bus.register(_IssueInvoice, _issue_invoice, uow_factory=_SuppressingUoW)
        command = _IssueInvoice(invoice_id=uuid4(), refuse=True)

        with pytest.raises(CommandExecutionError) as raised:
            await bus.dispatch(command)

        assert raised.value.command is command
        assert isinstance(raised.value.__cause__, _InvoiceRefused)

    @pytest.mark.xfail(
        strict=True,
        raises=UnboundLocalError,
        reason="DCE-128 review: a UoW exit that suppresses a cancellation "
        "leaves the result unbound",
    )
    @pytest.mark.anyio
    async def test_suppressing_uow_exit_still_raises_the_cancellation(
        self, bus: CommandBus
    ) -> None:
        class _SuppressingUoW(_InvoiceUoW):
            async def __aexit__(self, *exc_info: Any) -> bool:  # type: ignore[override]
                await super().__aexit__(*exc_info)
                return True

        async def interrupted_handler(
            cmd: _IssueInvoice, uow: _InvoiceUoW
        ) -> EmptyCommandResult:
            raise KeyboardInterrupt

        bus.register(_IssueInvoice, interrupted_handler, uow_factory=_SuppressingUoW)

        with pytest.raises(KeyboardInterrupt):
            await bus.dispatch(_IssueInvoice(invoice_id=uuid4()))

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-128 review: raise ... from None drops the cancellation's "
        "own __cause__",
    )
    @pytest.mark.anyio
    async def test_cancellation_keeps_its_own_cause_when_the_rollback_raises(
        self, bus: CommandBus
    ) -> None:
        async def interrupted_handler(
            cmd: _IssueInvoice, uow: _InvoiceUoW
        ) -> EmptyCommandResult:
            try:
                raise _InvoiceRefused("synthetic origin")
            except _InvoiceRefused as origin:
                raise KeyboardInterrupt from origin

        bus.register(
            _IssueInvoice, interrupted_handler, uow_factory=_FailingRollbackUoW
        )

        with pytest.raises(KeyboardInterrupt) as raised:
            await bus.dispatch(_IssueInvoice(invoice_id=uuid4()))

        assert isinstance(raised.value.__cause__, _InvoiceRefused)
        assert isinstance(raised.value.__context__, _RollbackFailed)
        assert raised.value.__notes__ == ["clean-up also failed: _RollbackFailed"]
        shown = "".join(traceback.format_exception(raised.value))
        assert "synthetic origin" in shown
        assert "synthetic-rollback-detail" not in shown

    @pytest.mark.anyio
    async def test_keyboard_interrupt_with_raising_rollback_propagates_with_note(
        self, bus: CommandBus
    ) -> None:
        async def interrupted_handler(
            cmd: _IssueInvoice, uow: _InvoiceUoW
        ) -> EmptyCommandResult:
            raise KeyboardInterrupt

        bus.register(
            _IssueInvoice, interrupted_handler, uow_factory=_FailingRollbackUoW
        )

        with pytest.raises(KeyboardInterrupt) as raised:
            await bus.dispatch(_IssueInvoice(invoice_id=uuid4()))

        assert raised.value.__notes__ == ["clean-up also failed: _RollbackFailed"]
        shown = "".join(traceback.format_exception(raised.value))
        assert "synthetic-rollback-detail" not in shown

    @pytest.mark.anyio
    async def test_timeout_around_a_dispatch_with_raising_rollback_stays_a_timeout(
        self, bus: CommandBus
    ) -> None:
        bus.register(_IssueInvoice, _sleeping_handler, uow_factory=_FailingRollbackUoW)

        with pytest.raises(TimeoutError) as raised:
            async with asyncio.timeout(0.01):
                await bus.dispatch(_IssueInvoice(invoice_id=uuid4()))

        cancelled = raised.value.__cause__
        assert isinstance(cancelled, asyncio.CancelledError)
        assert cancelled.__notes__ == ["clean-up also failed: _RollbackFailed"]
        shown = "".join(traceback.format_exception(raised.value))
        assert "synthetic-rollback-detail" not in shown

    @pytest.mark.anyio
    async def test_nested_dispatch_under_timeout_stays_a_timeout(
        self, bus: CommandBus
    ) -> None:
        class _Outer(Command[EmptyCommandResult]):
            pass

        async def outer_handler(cmd: _Outer, uow: _InvoiceUoW) -> EmptyCommandResult:
            await bus.dispatch(_IssueInvoice(invoice_id=uuid4()))
            return EmptyCommandResult()

        bus.register(_IssueInvoice, _sleeping_handler, uow_factory=_FailingRollbackUoW)
        bus.register(_Outer, outer_handler, uow_factory=_FailingRollbackUoW)

        with pytest.raises(TimeoutError) as raised:
            async with asyncio.timeout(0.01):
                await bus.dispatch(_Outer())

        cancelled = raised.value.__cause__
        assert isinstance(cancelled, asyncio.CancelledError)
        assert cancelled.__notes__ == [  # one per rollback, inner then outer
            "clean-up also failed: _RollbackFailed",
            "clean-up also failed: _RollbackFailed",
        ]
        assert "synthetic-rollback-detail" not in "".join(
            traceback.format_exception(raised.value)
        )

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
    async def test_handler_commit_is_persisted_once_and_not_rolled_back(
        self, bus: CommandBus
    ) -> None:
        """D-CQRS-10: the first commit stands; the bus's commit writes nothing."""
        uow = _CountingUoW()

        async def committing_handler(
            cmd: _IssueInvoice, uow: _InvoiceUoW
        ) -> EmptyCommandResult:
            await _issue_invoice(cmd, uow)
            await uow.commit()  # against ADR-020: the bus owns the commit
            return EmptyCommandResult()

        bus.register(_IssueInvoice, committing_handler, uow_factory=lambda: uow)
        command = _IssueInvoice(invoice_id=uuid4())

        with pytest.raises(CommandExecutionError):
            await bus.dispatch(command)

        assert uow.commits == 1
        assert uow.rollbacks == 0
        assert await uow.invoices.get_by_id(command.invoice_id) is not None
        assert _invoice_ids(uow.collect_events()) == {command.invoice_id}

    @pytest.mark.anyio
    async def test_change_saved_after_a_handler_commit_never_reaches_next_dispatch(
        self, bus: CommandBus
    ) -> None:
        shared: FakeRepository[_Invoice, UUID] = FakeRepository()
        late_id = uuid4()

        async def committing_handler(
            cmd: _IssueInvoice, uow: _InvoiceUoW
        ) -> EmptyCommandResult:
            await _issue_invoice(cmd, uow)
            await uow.commit()  # against ADR-020: the bus owns the commit
            await uow.invoices.save(_Invoice.issue(late_id))
            return EmptyCommandResult()

        bus.register(
            _IssueInvoice, committing_handler, uow_factory=lambda: _InvoiceUoW(shared)
        )
        bus_next = CommandBus()
        bus_next.register(
            _IssueInvoice, _issue_invoice, uow_factory=lambda: _InvoiceUoW(shared)
        )

        with pytest.raises(CommandExecutionError):
            await bus.dispatch(_IssueInvoice(invoice_id=uuid4()))
        accepted = _IssueInvoice(invoice_id=uuid4())
        _, events = await bus_next.dispatch(accepted)

        assert _invoice_ids(events) == {accepted.invoice_id}

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

    @pytest.mark.anyio
    async def test_drain_failure_after_a_successful_commit_propagates_unwrapped(
        self, bus: CommandBus
    ) -> None:
        """Author decision B-20: unwrapped, no events returned, commit persisted."""

        class _BrokenRepo:
            def __init__(self) -> None:
                self.pulls = 0

            def pull_events(self) -> list[DomainEvent]:
                self.pulls += 1
                if self.pulls == 2:  # the commit pulls first, the exit drain next
                    raise _RollbackFailed
                return []

        uow = _CountingUoW()
        uow._repos["broken"] = _BrokenRepo()
        bus.register(_IssueInvoice, _issue_invoice, uow_factory=lambda: uow)
        command = _IssueInvoice(invoice_id=uuid4())

        with pytest.raises(_RollbackFailed):
            await bus.dispatch(command)

        assert uow.commits == 1
        assert uow.rollbacks == 0
        assert await uow.invoices.get_by_id(command.invoice_id) is not None


class _CapturingBehavior:
    """Records the context each dispatch hands the pipeline."""

    def __init__(self) -> None:
        self.contexts: list[MessageContext] = []
        self.seen: list[dict[str, Any]] = []

    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        self.contexts.append(ctx)
        self.seen.append(
            {
                "message": ctx.message,
                "kind": ctx.kind,
                "uow": ctx.uow,
                "correlation_id": ctx.correlation_id,
                "causation_id": ctx.causation_id,
                "metadata": dict(ctx.metadata),
            }
        )
        return await next()


# ═══════════════════════════════════════════════════════════════════════════
# B-19 (ADR-068 Negative): units of work running concurrently on one shared
# repository. A repository is shared only between units of work that run one
# after another; these tests pin what happens otherwise, the documented limit
# that backlog Feature F-6 (scope-keyed buffers) would lift. The order of the
# steps is forced with events, never with sleeps.
# ═══════════════════════════════════════════════════════════════════════════

_Handler = Any


class _LabelledInvoice(_IssueInvoice):
    label: str


class _GatedCommitUoW(_InvoiceUoW):
    """Holds its storage commit until *gate* is set."""

    def __init__(
        self, invoices: FakeRepository[_Invoice, UUID], gate: asyncio.Event
    ) -> None:
        super().__init__(invoices)
        self.gate = gate

    async def _commit(self) -> None:
        await self.gate.wait()


async def _race(
    shared: FakeRepository[_Invoice, UUID],
    handle_a: _Handler,
    handle_b: _Handler,
    *,
    a_refuses: bool = False,
    a_commit_gate: asyncio.Event | None = None,
    a_finished: asyncio.Event | None = None,
    b_finished: asyncio.Event | None = None,
    lock_keys: tuple[str, str] | None = None,
) -> tuple[set[UUID] | str, set[UUID], UUID, UUID]:
    """Dispatch A and B concurrently on *shared*; return what each published."""
    behaviors: list[Any] = []
    if lock_keys is not None:
        keys = dict(zip(("A", "B"), lock_keys, strict=True))
        resolver = DictLockKeyResolver()
        resolver.register(_LabelledInvoice, lambda m: [keys[m.label]])
        behaviors = [AggregateLockingBehavior(FakeLockProvider(), resolver)]
    gate = a_commit_gate
    bus_a, bus_b = CommandBus(), CommandBus()
    bus_a.register(
        _LabelledInvoice,
        handle_a,
        uow_factory=lambda: (
            _GatedCommitUoW(shared, gate) if gate is not None else _InvoiceUoW(shared)
        ),
        behaviors=behaviors,
    )
    bus_b.register(
        _LabelledInvoice,
        handle_b,
        uow_factory=lambda: _InvoiceUoW(shared),
        behaviors=behaviors,
    )
    a = _LabelledInvoice(invoice_id=uuid4(), label="A", refuse=a_refuses)
    b = _LabelledInvoice(invoice_id=uuid4(), label="B")

    async def dispatch_a() -> set[UUID] | str:
        try:
            _, events = await bus_a.dispatch(a)
        except CommandExecutionError:
            return "refused"
        finally:
            if a_finished is not None:
                a_finished.set()
        return _invoice_ids(events)

    async def dispatch_b() -> set[UUID]:
        try:
            _, events = await bus_b.dispatch(b)
        finally:
            if b_finished is not None:
                b_finished.set()
        return _invoice_ids(events)

    result_a, result_b = await asyncio.gather(dispatch_a(), dispatch_b())
    return result_a, result_b, a.invoice_id, b.invoice_id


async def _save(cmd: _LabelledInvoice, uow: _InvoiceUoW) -> None:
    await uow.invoices.save(_Invoice.issue(cmd.invoice_id))


class TestSharedRepositoryAcrossConcurrentUnitsOfWork:
    """The documented limit of B-19: one dispatch's drain or commit can take
    the other's events."""

    @pytest.mark.anyio
    async def test_rollback_of_a_refused_dispatch_drops_the_others_event(
        self,
    ) -> None:
        shared: FakeRepository[_Invoice, UUID] = FakeRepository()
        a_saved, b_saved, a_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def handle_a(
            cmd: _LabelledInvoice, uow: _InvoiceUoW
        ) -> EmptyCommandResult:
            await _save(cmd, uow)
            a_saved.set()
            await b_saved.wait()
            raise _InvoiceRefused("synthetic refusal")

        async def handle_b(
            cmd: _LabelledInvoice, uow: _InvoiceUoW
        ) -> EmptyCommandResult:
            await a_saved.wait()
            await _save(cmd, uow)
            b_saved.set()
            await a_finished.wait()
            return EmptyCommandResult()

        result_a, result_b, _, _ = await _race(
            shared, handle_a, handle_b, a_refuses=True, a_finished=a_finished
        )

        assert result_a == "refused"
        assert result_b == set()  # B's event was drained by A's rollback

    @pytest.mark.anyio
    async def test_committed_exit_drain_drops_an_event_saved_during_the_commit(
        self,
    ) -> None:
        shared: FakeRepository[_Invoice, UUID] = FakeRepository()
        a_saved, a_finished, gate = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def handle_a(
            cmd: _LabelledInvoice, uow: _InvoiceUoW
        ) -> EmptyCommandResult:
            await _save(cmd, uow)
            a_saved.set()
            return EmptyCommandResult()

        async def handle_b(
            cmd: _LabelledInvoice, uow: _InvoiceUoW
        ) -> EmptyCommandResult:
            await a_saved.wait()
            await _save(cmd, uow)
            gate.set()  # A's storage commit finishes after B saved
            await a_finished.wait()
            return EmptyCommandResult()

        result_a, result_b, a_id, _ = await _race(
            shared, handle_a, handle_b, a_commit_gate=gate, a_finished=a_finished
        )

        assert result_a == {a_id}
        assert result_b == set()  # B's event was drained on A's committed exit

    @pytest.mark.anyio
    async def test_first_commit_takes_both_dispatches_events(self) -> None:
        shared: FakeRepository[_Invoice, UUID] = FakeRepository()
        a_saved, b_saved, a_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def handle_a(
            cmd: _LabelledInvoice, uow: _InvoiceUoW
        ) -> EmptyCommandResult:
            await _save(cmd, uow)
            a_saved.set()
            await b_saved.wait()
            return EmptyCommandResult()

        async def handle_b(
            cmd: _LabelledInvoice, uow: _InvoiceUoW
        ) -> EmptyCommandResult:
            await a_saved.wait()
            await _save(cmd, uow)
            b_saved.set()
            await a_finished.wait()
            return EmptyCommandResult()

        result_a, result_b, a_id, b_id = await _race(
            shared, handle_a, handle_b, a_finished=a_finished
        )

        assert result_a == {a_id, b_id}  # stamped with A's command, not B's
        assert result_b == set()

    @pytest.mark.parametrize(
        "a_refuses", [True, False], ids=["a-refused", "a-accepted"]
    )
    @pytest.mark.parametrize(
        "lock_keys",
        [None, ("invoice-1", "invoice-2")],
        ids=["no-locks", "locks-on-different-keys"],
    )
    @pytest.mark.anyio
    async def test_later_commit_publishes_an_earlier_dispatchs_event(
        self, lock_keys: tuple[str, str] | None, a_refuses: bool
    ) -> None:
        """B commits while A is still running, so B's commit publishes A's
        event under B's command: B-19's "published under the wrong command".
        When A is then refused, that is a refused command's event. A, accepted
        or not, publishes nothing. AggregateLockingBehavior does not prevent
        it: the two dispatches lock different aggregates, so they still run
        concurrently."""
        shared: FakeRepository[_Invoice, UUID] = FakeRepository()
        a_saved, b_finished = asyncio.Event(), asyncio.Event()

        async def handle_a(
            cmd: _LabelledInvoice, uow: _InvoiceUoW
        ) -> EmptyCommandResult:
            await _save(cmd, uow)
            a_saved.set()
            await b_finished.wait()
            if cmd.refuse:
                raise _InvoiceRefused("synthetic refusal")
            return EmptyCommandResult()

        async def handle_b(
            cmd: _LabelledInvoice, uow: _InvoiceUoW
        ) -> EmptyCommandResult:
            await a_saved.wait()
            await _save(cmd, uow)
            return EmptyCommandResult()

        result_a, result_b, a_id, b_id = await _race(
            shared,
            handle_a,
            handle_b,
            a_refuses=a_refuses,
            b_finished=b_finished,
            lock_keys=lock_keys,
        )

        assert result_a == ("refused" if a_refuses else set())
        assert result_b == {a_id, b_id}  # B published A's invoice


class TestMessageContext:
    """What the bus hands every pipeline behavior (ADR-017, ADR-021)."""

    @pytest.mark.anyio
    async def test_context_carries_the_command_uow_and_tracing_ids(
        self, bus: CommandBus
    ) -> None:
        created: list[_InvoiceUoW] = []

        def factory() -> _InvoiceUoW:
            created.append(_InvoiceUoW())
            return created[-1]

        behavior = _CapturingBehavior()
        bus.register(
            _IssueInvoice, _issue_invoice, uow_factory=factory, behaviors=[behavior]
        )
        command = _IssueInvoice(invoice_id=uuid4())

        await bus.dispatch(command)

        assert behavior.seen == [
            {
                "message": command,
                "kind": MessageKind.COMMAND,
                "uow": created[0],
                "correlation_id": command.command_id,
                "causation_id": command.command_id,
                "metadata": {"command_id": command.command_id},
            }
        ]

    @pytest.mark.anyio
    async def test_context_prefers_the_commands_own_tracing_ids(
        self, bus: CommandBus
    ) -> None:
        behavior = _CapturingBehavior()
        bus.register(
            _IssueInvoice, _issue_invoice, uow_factory=_InvoiceUoW, behaviors=[behavior]
        )
        correlation_id, causation_id = uuid4(), uuid4()
        command = _IssueInvoice(
            invoice_id=uuid4(), correlation_id=correlation_id, causation_id=causation_id
        )

        await bus.dispatch(command)

        assert behavior.seen[0]["correlation_id"] == correlation_id
        assert behavior.seen[0]["causation_id"] == causation_id

    @pytest.mark.anyio
    async def test_context_holds_the_returned_events_after_dispatch(
        self, bus: CommandBus
    ) -> None:
        behavior = _CapturingBehavior()
        bus.register(
            _IssueInvoice, _issue_invoice, uow_factory=_InvoiceUoW, behaviors=[behavior]
        )

        _, events = await bus.dispatch(_IssueInvoice(invoice_id=uuid4()))

        assert len(events) == 1
        assert behavior.contexts[0].new_events == events


class TestErrorMessages:
    @pytest.mark.anyio
    async def test_unregistered_command_names_the_command_type(
        self, bus: CommandBus
    ) -> None:
        with pytest.raises(NoHandlerRegisteredError) as raised:
            await bus.dispatch(MakeGreeting(name="World"))

        assert str(raised.value) == "No handler registered for MakeGreeting"

    def test_duplicate_registration_names_the_command_type(
        self, bus: CommandBus
    ) -> None:
        async def handler(cmd: MakeGreeting, uow: Any) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register(MakeGreeting, handler, uow_factory=FakeUnitOfWork)

        with pytest.raises(HandlerAlreadyRegisteredError) as raised:
            bus.register(MakeGreeting, handler, uow_factory=FakeUnitOfWork)

        assert str(raised.value) == "Handler already registered for MakeGreeting"
