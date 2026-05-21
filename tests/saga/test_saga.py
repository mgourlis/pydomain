"""Tests for Saga base class —
all handling styles, lifecycle, compensation, configuration."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from pydomain.cqrs.saga.exceptions import (
    SagaConfigurationError,
    SagaHandlerNotFoundError,
)
from pydomain.cqrs.saga.saga import Saga
from pydomain.cqrs.saga.state import SagaState, SagaStatus
from pydomain.ddd.domain_event import DomainEvent

from .conftest import (
    ApprovalGranted,
    ApprovalRequested,
    CancelOrder,
    CancelPayment,
    CancelReservation,
    HandlerStyleSaga,
    ItemsReserved,
    MultiDispatchSaga,
    NoListenSaga,
    OrderCreated,
    OrderFailed,
    OverrideHandleEventSaga,
    ProcessPayment,
    ReserveItems,
    SendNotification,
    SuspendableSaga,
    TimeoutRetrySaga,
    TwoStepSaga,
)

# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def saga_state() -> SagaState:
    return SagaState(saga_type="TwoStepSaga", correlation_id=uuid4())


@pytest.fixture
def two_step_saga(saga_state: SagaState) -> TwoStepSaga:
    return TwoStepSaga(saga_state)


@pytest.fixture
def handler_saga(saga_state: SagaState) -> HandlerStyleSaga:
    return HandlerStyleSaga(saga_state)


@pytest.fixture
def override_saga(saga_state: SagaState) -> OverrideHandleEventSaga:
    return OverrideHandleEventSaga(saga_state)


@pytest.fixture
def multi_dispatch_saga(saga_state: SagaState) -> MultiDispatchSaga:
    return MultiDispatchSaga(saga_state)


@pytest.fixture
def suspendable_saga(saga_state: SagaState) -> SuspendableSaga:
    return SuspendableSaga(saga_state)


@pytest.fixture
def timeout_retry_saga() -> TimeoutRetrySaga:
    state = SagaState(
        saga_type="TimeoutRetrySaga",
        status=SagaStatus.SUSPENDED,
        suspension_reason="waiting",
    )
    return TimeoutRetrySaga(state)


# ═══════════════════════════════════════════════════════════════════════
# handle() — Idempotent Entry Point
# ═══════════════════════════════════════════════════════════════════════


class TestSagaHandleIdempotency:
    """handle() is idempotent — skips already-processed events and terminal states."""

    @pytest.mark.anyio
    async def test_handle_transitions_pending_to_running(
        self, two_step_saga: TwoStepSaga
    ) -> None:
        assert two_step_saga.state.status == SagaStatus.PENDING
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await two_step_saga.handle(event)
        assert two_step_saga.state.status == SagaStatus.RUNNING

    @pytest.mark.anyio
    async def test_handle_skips_already_processed_event(
        self, two_step_saga: TwoStepSaga
    ) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await two_step_saga.handle(event)
        _commands1 = two_step_saga.collect_commands()  # noqa: F841

        # Same event again — should be skipped
        await two_step_saga.handle(event)
        commands2 = two_step_saga.collect_commands()
        assert len(commands2) == 0

    @pytest.mark.anyio
    async def test_handle_skips_terminal_state_completed(
        self, saga_state: SagaState
    ) -> None:
        saga_state.status = SagaStatus.COMPLETED
        saga = TwoStepSaga(saga_state)
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        assert saga.collect_commands() == []

    @pytest.mark.anyio
    async def test_handle_skips_terminal_state_failed(
        self, saga_state: SagaState
    ) -> None:
        saga_state.status = SagaStatus.FAILED
        saga = TwoStepSaga(saga_state)
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        assert saga.collect_commands() == []

    @pytest.mark.anyio
    async def test_handle_skips_terminal_state_compensated(
        self, saga_state: SagaState
    ) -> None:
        saga_state.status = SagaStatus.COMPENSATED
        saga = TwoStepSaga(saga_state)
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        assert saga.collect_commands() == []

    @pytest.mark.anyio
    async def test_handle_records_step(self, two_step_saga: TwoStepSaga) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await two_step_saga.handle(event)
        assert len(two_step_saga.state.step_history) == 1
        assert two_step_saga.state.step_history[0].step_name == "reserving"

    @pytest.mark.anyio
    async def test_handle_marks_event_processed(
        self, two_step_saga: TwoStepSaga
    ) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await two_step_saga.handle(event)
        assert two_step_saga.state.is_event_processed(event.event_id)


# ═══════════════════════════════════════════════════════════════════════
# on() — Command-Mapper Style
# ═══════════════════════════════════════════════════════════════════════


class TestSagaCommandMapperStyle:
    """on(send=...) — declarative event-to-command mapping."""

    @pytest.mark.anyio
    async def test_send_dispatches_command(self, two_step_saga: TwoStepSaga) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await two_step_saga.handle(event)
        commands = two_step_saga.collect_commands()
        assert len(commands) == 1
        assert isinstance(commands[0], ReserveItems)

    @pytest.mark.anyio
    async def test_step_parameter_updates_current_step(
        self, two_step_saga: TwoStepSaga
    ) -> None:
        assert two_step_saga.state.current_step == "init"
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await two_step_saga.handle(event)
        assert two_step_saga.state.current_step == "reserving"

    @pytest.mark.anyio
    async def test_compensate_adds_to_stack(self, two_step_saga: TwoStepSaga) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await two_step_saga.handle(event)
        assert len(two_step_saga.state.compensation_stack) == 1
        assert (
            two_step_saga.state.compensation_stack[0].command_type
            == "CancelReservation"
        )

    @pytest.mark.anyio
    async def test_complete_flag_marks_completed(
        self, two_step_saga: TwoStepSaga
    ) -> None:
        cid = uuid4()
        await two_step_saga.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        _ = two_step_saga.collect_commands()
        await two_step_saga.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))
        assert two_step_saga.state.status == SagaStatus.COMPLETED
        assert two_step_saga.state.completed_at is not None


# ═══════════════════════════════════════════════════════════════════════
# on() — Handler Style
# ═══════════════════════════════════════════════════════════════════════


class TestSagaHandlerStyle:
    """on(handler=...) — custom handler for complex logic."""

    @pytest.mark.anyio
    async def test_handler_dispatches_command(
        self, handler_saga: HandlerStyleSaga
    ) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await handler_saga.handle(event)
        commands = handler_saga.collect_commands()
        assert len(commands) == 1
        assert isinstance(commands[0], ReserveItems)


# ═══════════════════════════════════════════════════════════════════════
# _handle_event Override Style
# ═══════════════════════════════════════════════════════════════════════


class TestSagaOverrideStyle:
    """Override _handle_event() for imperative match/case dispatch."""

    @pytest.mark.anyio
    async def test_match_dispatch(self, override_saga: OverrideHandleEventSaga) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await override_saga.handle(event)
        commands = override_saga.collect_commands()
        assert len(commands) == 1
        assert isinstance(commands[0], ReserveItems)

    @pytest.mark.anyio
    async def test_match_complete(self, override_saga: OverrideHandleEventSaga) -> None:
        cid = uuid4()
        await override_saga.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        _ = override_saga.collect_commands()
        await override_saga.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))
        assert override_saga.state.status == SagaStatus.COMPLETED

    @pytest.mark.anyio
    async def test_match_fail(self, override_saga: OverrideHandleEventSaga) -> None:
        event = OrderFailed(
            order_id="ORD-1", reason="Payment declined", correlation_id=uuid4()
        )
        await override_saga.handle(event)
        assert override_saga.state.status == SagaStatus.FAILED
        assert override_saga.state.error == "Payment declined"

    @pytest.mark.anyio
    async def test_match_unknown_silent(
        self, override_saga: OverrideHandleEventSaga
    ) -> None:
        """Unknown events in override style are silently ignored."""
        event = ApprovalRequested(order_id="ORD-1", correlation_id=uuid4())
        await override_saga.handle(event)
        assert override_saga.collect_commands() == []


# ═══════════════════════════════════════════════════════════════════════
# on() — HandlerNotFoundError
# ═══════════════════════════════════════════════════════════════════════


class TestSagaHandlerNotFound:
    """Unregistered event type raises SagaHandlerNotFoundError."""

    @pytest.mark.anyio
    async def test_unregistered_event_raises(self, two_step_saga: TwoStepSaga) -> None:
        event = ApprovalGranted(order_id="ORD-1", correlation_id=uuid4())
        with pytest.raises(SagaHandlerNotFoundError, match="No handler registered"):
            await two_step_saga.handle(event)


# ═══════════════════════════════════════════════════════════════════════
# on() — Configuration Validation
# ═══════════════════════════════════════════════════════════════════════


class TestSagaConfiguration:
    """on() raises SagaConfigurationError for invalid setups."""

    def test_both_handler_and_send_raises(self, saga_state: SagaState) -> None:
        saga = TwoStepSaga(saga_state)
        with pytest.raises(SagaConfigurationError, match="Cannot provide both"):
            saga.on(
                OrderCreated,
                handler=lambda e: None,
                send=lambda e: ReserveItems(order_id="x", item_count=1),
            )

    def test_neither_handler_nor_send_raises(self, saga_state: SagaState) -> None:
        saga = TwoStepSaga(saga_state)
        with pytest.raises(SagaConfigurationError, match="Must provide either"):
            saga.on(OrderCreated)

    def test_on_registers_handler(self, saga_state: SagaState) -> None:
        """Subsequent on() for same event type overwrites."""
        saga = TwoStepSaga(saga_state)
        # Re-register with different handler
        saga.on(OrderCreated, handler=lambda e: None)
        # The last registration wins
        assert OrderCreated in saga._event_handlers


# ═══════════════════════════════════════════════════════════════════════
# dispatch() and collect_commands()
# ═══════════════════════════════════════════════════════════════════════


class TestSagaDispatch:
    """Command queuing and collection."""

    @pytest.mark.anyio
    async def test_dispatch_queues_command(self, two_step_saga: TwoStepSaga) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await two_step_saga.handle(event)
        commands = two_step_saga.collect_commands()
        assert len(commands) == 1

    def test_collect_commands_clears_queue(self, two_step_saga: TwoStepSaga) -> None:
        two_step_saga.dispatch(ReserveItems(order_id="X"))
        cmds1 = two_step_saga.collect_commands()
        assert len(cmds1) == 1
        cmds2 = two_step_saga.collect_commands()
        assert len(cmds2) == 0

    @pytest.mark.anyio
    async def test_multiple_dispatches(self) -> None:
        state = SagaState(saga_type="MultiDispatchSaga", correlation_id=uuid4())
        saga = MultiDispatchSaga(state)
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        commands = saga.collect_commands()
        assert len(commands) == 2
        types = {type(c) for c in commands}
        assert ReserveItems in types
        assert SendNotification in types


# ═══════════════════════════════════════════════════════════════════════
# complete()
# ═══════════════════════════════════════════════════════════════════════


class TestSagaComplete:
    """complete() sets COMPLETED and completed_at."""

    def test_complete_sets_status(self, two_step_saga: TwoStepSaga) -> None:
        two_step_saga.complete()
        assert two_step_saga.state.status == SagaStatus.COMPLETED

    def test_complete_sets_timestamp(self, two_step_saga: TwoStepSaga) -> None:
        assert two_step_saga.state.completed_at is None
        two_step_saga.complete()
        assert two_step_saga.state.completed_at is not None

    def test_complete_touches(self, two_step_saga: TwoStepSaga) -> None:
        v_before = two_step_saga.state.version
        two_step_saga.complete()
        assert two_step_saga.state.version > v_before


# ═══════════════════════════════════════════════════════════════════════
# fail()
# ═══════════════════════════════════════════════════════════════════════


class TestSagaFail:
    """fail() sets error, failed_at, and triggers compensation."""

    @pytest.mark.anyio
    async def test_fail_without_compensation(self, two_step_saga: TwoStepSaga) -> None:
        await two_step_saga.fail("something broke", compensate=False)
        assert two_step_saga.state.status == SagaStatus.FAILED
        assert two_step_saga.state.error == "something broke"
        assert two_step_saga.state.failed_at is not None

    @pytest.mark.anyio
    async def test_fail_with_empty_compensation_stack(
        self, two_step_saga: TwoStepSaga
    ) -> None:
        """Empty compensation stack → FAILED directly."""
        await two_step_saga.fail("nothing to compensate")
        assert two_step_saga.state.status == SagaStatus.FAILED

    @pytest.mark.anyio
    async def test_fail_with_compensation_triggers_compensating(
        self, two_step_saga: TwoStepSaga
    ) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await two_step_saga.handle(event)
        _ = two_step_saga.collect_commands()
        assert len(two_step_saga.state.compensation_stack) == 1
        await two_step_saga.fail("payment failed")
        assert two_step_saga.state.status == SagaStatus.COMPENSATING
        assert two_step_saga.state.error == "payment failed"
        commands = two_step_saga.collect_commands()
        assert len(commands) == 1
        assert isinstance(commands[0], CancelReservation)

    @pytest.mark.anyio
    async def test_fail_sets_timestamp(self, two_step_saga: TwoStepSaga) -> None:
        assert two_step_saga.state.failed_at is None
        await two_step_saga.fail("error")
        assert two_step_saga.state.failed_at is not None


# ═══════════════════════════════════════════════════════════════════════
# suspend() and resume()
# ═══════════════════════════════════════════════════════════════════════


class TestSagaSuspendResume:
    """suspend() and resume() lifecycle transitions."""

    def test_suspend_with_timeout(self, two_step_saga: TwoStepSaga) -> None:
        two_step_saga.suspend("waiting for approval", timeout=timedelta(hours=1))
        assert two_step_saga.state.status == SagaStatus.SUSPENDED
        assert two_step_saga.state.suspension_reason == "waiting for approval"
        assert two_step_saga.state.suspended_at is not None
        assert two_step_saga.state.timeout_at is not None

    def test_suspend_without_timeout(self, two_step_saga: TwoStepSaga) -> None:
        two_step_saga.suspend("waiting indefinitely")
        assert two_step_saga.state.status == SagaStatus.SUSPENDED
        assert two_step_saga.state.timeout_at is None

    def test_resume_transitions_to_running(self, two_step_saga: TwoStepSaga) -> None:
        two_step_saga.suspend("waiting")
        two_step_saga.resume()
        assert two_step_saga.state.status == SagaStatus.RUNNING
        assert two_step_saga.state.suspension_reason is None
        assert two_step_saga.state.suspended_at is None

    def test_resume_non_suspended_is_noop(self, two_step_saga: TwoStepSaga) -> None:
        two_step_saga.resume()
        assert two_step_saga.state.status == SagaStatus.PENDING

    @pytest.mark.anyio
    async def test_suspend_via_on_declarative(self) -> None:
        """on(suspend=True) suspends after handling the event."""
        state = SagaState(saga_type="SuspendableSaga", correlation_id=uuid4())
        saga = SuspendableSaga(state)
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        assert saga.state.status == SagaStatus.SUSPENDED
        assert saga.state.suspension_reason == "Waiting for manager approval"
        assert saga.state.timeout_at is not None


# ═══════════════════════════════════════════════════════════════════════
# add_compensation() and execute_compensations()
# ═══════════════════════════════════════════════════════════════════════


class TestSagaCompensation:
    """Compensation stack management and LIFO execution."""

    def test_add_compensation_manual(self, two_step_saga: TwoStepSaga) -> None:
        cmd = CancelOrder(order_id="ORD-1")
        two_step_saga.add_compensation(cmd, description="Cancel entire order")
        assert len(two_step_saga.state.compensation_stack) == 1
        rec = two_step_saga.state.compensation_stack[0]
        assert rec.command_type == "CancelOrder"
        assert rec.description == "Cancel entire order"

    @pytest.mark.anyio
    async def test_compensation_added_on_event(
        self, two_step_saga: TwoStepSaga
    ) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await two_step_saga.handle(event)
        assert len(two_step_saga.state.compensation_stack) == 1

    @pytest.mark.anyio
    async def test_execute_compensations_drains_stack(
        self, two_step_saga: TwoStepSaga
    ) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await two_step_saga.handle(event)
        _ = two_step_saga.collect_commands()
        assert len(two_step_saga.state.compensation_stack) == 1
        await two_step_saga.execute_compensations()
        assert len(two_step_saga.state.compensation_stack) == 0
        assert two_step_saga.state.status == SagaStatus.COMPENSATING
        commands = two_step_saga.collect_commands()
        assert len(commands) == 1
        assert isinstance(commands[0], CancelReservation)

    @pytest.mark.anyio
    async def test_compensation_lifo_order(self) -> None:
        """Compensations execute in LIFO (reverse) order."""
        state = SagaState(saga_type="TestSaga", correlation_id=uuid4())
        saga = Saga(state)

        async def step1(evt: DomainEvent) -> None:
            saga.dispatch(ReserveItems(order_id="ORD-1"))
            saga.add_compensation(
                CancelReservation(order_id="ORD-1"), "Cancel reservation"
            )

        async def step2(evt: DomainEvent) -> None:
            saga.dispatch(ProcessPayment(order_id="ORD-1"))
            saga.add_compensation(CancelPayment(order_id="ORD-1"), "Cancel payment")

        saga.on(OrderCreated, handler=step1)
        saga.on(ItemsReserved, handler=step2)

        cid = uuid4()
        await saga.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        _ = saga.collect_commands()
        await saga.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))
        _ = saga.collect_commands()

        # Stack should have 2 compensations
        assert len(saga.state.compensation_stack) == 2
        # LIFO: CancelPayment was added last, so it pops first
        await saga.execute_compensations()
        commands = saga.collect_commands()
        assert len(commands) == 2
        # First popped = last added
        assert isinstance(commands[0], CancelPayment)
        assert isinstance(commands[1], CancelReservation)


# ═══════════════════════════════════════════════════════════════════════
# on_timeout()
# ═══════════════════════════════════════════════════════════════════════


class TestSagaOnTimeout:
    """on_timeout() — default and overridden behaviour."""

    @pytest.mark.anyio
    async def test_on_timeout_calls_fail_by_default(
        self, two_step_saga: TwoStepSaga
    ) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await two_step_saga.handle(event)
        _ = two_step_saga.collect_commands()
        two_step_saga.suspend("waiting for payment")
        await two_step_saga.on_timeout()
        assert two_step_saga.state.status == SagaStatus.COMPENSATING
        assert "Saga timed out while suspended" in two_step_saga.state.error
        assert "waiting for payment" in two_step_saga.state.error

    @pytest.mark.anyio
    async def test_on_timeout_custom_override(
        self, timeout_retry_saga: TimeoutRetrySaga
    ) -> None:
        await timeout_retry_saga.on_timeout()
        assert timeout_retry_saga.state.status == SagaStatus.RUNNING
        commands = timeout_retry_saga.collect_commands()
        assert len(commands) == 1
        assert isinstance(commands[0], ReserveItems)
        assert commands[0].order_id == "ORD-RETRY"

    @pytest.mark.anyio
    async def test_on_timeout_no_compensation_empty_stack(
        self, two_step_saga: TwoStepSaga
    ) -> None:
        two_step_saga.suspend("waiting")
        await two_step_saga.on_timeout()
        # No compensations → FAILED directly
        assert two_step_saga.state.status == SagaStatus.FAILED
        assert "timed out" in (two_step_saga.state.error or "").lower()


# ═══════════════════════════════════════════════════════════════════════
# listened_events()
# ═══════════════════════════════════════════════════════════════════════


class TestSagaListenedEvents:
    """listened_events() class method."""

    def test_from_subclass(self) -> None:
        assert OrderCreated in TwoStepSaga.listened_events()
        assert ItemsReserved in TwoStepSaga.listened_events()

    def test_base_saga_has_no_events(self) -> None:
        assert Saga.listened_events() == []

    def test_no_listen_saga(self) -> None:
        assert NoListenSaga.listened_events() == []

    def test_five_step_saga(self) -> None:
        from .conftest import FiveStepSaga

        events = FiveStepSaga.listened_events()
        assert len(events) == 5


# ═══════════════════════════════════════════════════════════════════════
# State class attribute
# ═══════════════════════════════════════════════════════════════════════


class TestSagaStateClass:
    """state_class defaults to SagaState."""

    def test_default_state_class(self) -> None:
        assert Saga.state_class is SagaState

    def test_custom_state_class(self) -> None:
        class CustomState(SagaState):
            custom_field: str = "test"

        class CustomSaga(Saga[CustomState]):
            state_class = CustomState
            listens_to = []

        assert CustomSaga.state_class is CustomState


# ═══════════════════════════════════════════════════════════════════════
# Coverage gap: terminal state early return
# ═══════════════════════════════════════════════════════════════════════


class TestTerminalStateEarlyReturn:
    """handle() returns early when saga is in a terminal state."""

    @pytest.mark.anyio
    async def test_completed_saga_ignores_event(
        self, two_step_saga: TwoStepSaga
    ) -> None:
        cid = uuid4()
        await two_step_saga.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        _ = two_step_saga.collect_commands()
        await two_step_saga.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))
        _ = two_step_saga.collect_commands()
        assert two_step_saga.state.status == SagaStatus.COMPLETED

        # Sending another event does nothing
        await two_step_saga.handle(OrderCreated(order_id="ORD-2", correlation_id=cid))
        assert two_step_saga.collect_commands() == []

    @pytest.mark.anyio
    async def test_failed_saga_ignores_event(self, two_step_saga: TwoStepSaga) -> None:
        await two_step_saga.fail("borked", compensate=False)
        assert two_step_saga.state.status == SagaStatus.FAILED

        await two_step_saga.handle(
            OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        )
        assert two_step_saga.collect_commands() == []


# ═══════════════════════════════════════════════════════════════════════
# Coverage gap: duplicate event idempotency
# ═══════════════════════════════════════════════════════════════════════


class TestDuplicateEventIdempotency:
    """handle() returns early when the same event_id is seen twice."""

    @pytest.mark.anyio
    async def test_same_event_id_ignored_on_second_delivery(
        self, two_step_saga: TwoStepSaga
    ) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await two_step_saga.handle(event)
        commands = two_step_saga.collect_commands()
        assert len(commands) == 1

        # Re-deliver the same event — should be ignored
        await two_step_saga.handle(event)
        assert two_step_saga.collect_commands() == []


# ═══════════════════════════════════════════════════════════════════════
# Coverage gap: on() complete branch
# ═══════════════════════════════════════════════════════════════════════


class TestOnCompleteBranch:
    """on(complete=True) marks the saga as COMPLETED after processing."""

    @pytest.mark.anyio
    async def test_on_complete_marks_completed(self) -> None:
        state = SagaState(saga_type="TestSaga", correlation_id=uuid4())
        saga = Saga(state)
        saga.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id="ORD-1"),
            complete=True,
        )
        await saga.handle(OrderCreated(order_id="ORD-1", correlation_id=uuid4()))
        assert saga.state.status == SagaStatus.COMPLETED
        assert saga.state.completed_at is not None

    @pytest.mark.anyio
    async def test_on_send_complete(self) -> None:
        state = SagaState(saga_type="TestSaga", correlation_id=uuid4())
        saga = Saga(state)
        saga.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id="ORD-1"),
            complete=True,
        )
        await saga.handle(OrderCreated(order_id="ORD-1", correlation_id=uuid4()))
        assert saga.state.status == SagaStatus.COMPLETED
        commands = saga.collect_commands()
        assert len(commands) == 1


# ═══════════════════════════════════════════════════════════════════════
# Coverage gap: execute_compensations hydration failure
# ═══════════════════════════════════════════════════════════════════════


class TestExecuteCompensationsHydrationFailure:
    """execute_compensations() records failed compensations for bad module."""

    @pytest.mark.anyio
    async def test_bad_module_records_failed_compensation(self) -> None:
        state = SagaState(saga_type="TestSaga", correlation_id=uuid4())
        saga = Saga(state)
        saga.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")
        # Corrupt the module name so hydration fails
        saga.state.compensation_stack[0] = saga.state.compensation_stack[0].model_copy(
            update={"module_name": "nonexistent.module"}
        )

        await saga.execute_compensations()

        assert saga.state.status == SagaStatus.COMPENSATING
        assert len(saga.state.failed_compensations) == 1
        assert saga.state.failed_compensations[0]["command_type"] == "CancelReservation"
