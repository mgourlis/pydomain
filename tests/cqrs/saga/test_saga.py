"""Tests for Saga base class — event handling, commands, compensation, lifecycle."""

from __future__ import annotations

from uuid import uuid4

import pytest

from pydomain.cqrs.commands import Command, EmptyCommandResult
from pydomain.cqrs.saga.exceptions import (
    SagaConfigurationError,
    SagaHandlerNotFoundError,
)
from pydomain.cqrs.saga.saga import Saga
from pydomain.cqrs.saga.state import SagaState, SagaStatus
from pydomain.ddd.domain_event import DomainEvent

# ── Test domain events ──────────────────────────────────────────────────


class OrderCreated(DomainEvent):
    order_id: str


class ItemsReserved(DomainEvent):
    order_id: str
    item_count: int


class PaymentCompleted(DomainEvent):
    order_id: str


class OrderFailed(DomainEvent):
    order_id: str
    reason: str


# ── Test commands ───────────────────────────────────────────────────────


class ReserveItems(Command[EmptyCommandResult]):
    order_id: str
    item_count: int


class ConfirmOrder(Command[EmptyCommandResult]):
    order_id: str


class CancelReservation(Command[EmptyCommandResult]):
    order_id: str


class CancelOrder(Command[EmptyCommandResult]):
    order_id: str


# ── Test saga subclass ──────────────────────────────────────────────────


class OrderFulfillmentSaga(Saga[SagaState]):
    """Concrete test saga — handles order creation to fulfillment."""

    listens_to = [OrderCreated, ItemsReserved, PaymentCompleted]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id, item_count=5),
            step="reserving",
            compensate=lambda e: CancelReservation(order_id=e.order_id),
            compensate_description="Cancel item reservation",
        )
        self.on(
            ItemsReserved,
            send=lambda e: ConfirmOrder(order_id=e.order_id),
            step="confirming",
            complete=True,
        )


class HandlerStyleSaga(Saga[SagaState]):
    """Saga using handler-style event mapping."""

    listens_to = [OrderCreated]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(OrderCreated, handler=self._on_order_created)

    async def _on_order_created(self, event: DomainEvent) -> None:
        self.dispatch(ReserveItems(order_id="test", item_count=1))


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def saga_state() -> SagaState:
    return SagaState(saga_type="OrderFulfillmentSaga", correlation_id=uuid4())


@pytest.fixture
def saga(saga_state: SagaState) -> OrderFulfillmentSaga:
    return OrderFulfillmentSaga(saga_state)


# ── Tests ───────────────────────────────────────────────────────────────


class TestSagaHandle:
    """Saga.handle() — the idempotent entry point."""

    @pytest.mark.anyio
    async def test_handle_transitions_to_running(
        self, saga: OrderFulfillmentSaga
    ) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        assert saga.state.status == SagaStatus.RUNNING

    @pytest.mark.anyio
    async def test_handle_records_event_as_processed(
        self, saga: OrderFulfillmentSaga
    ) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        assert saga.state.is_event_processed(event.event_id)

    @pytest.mark.anyio
    async def test_handle_skips_already_processed_event(
        self, saga: OrderFulfillmentSaga
    ) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        # Handle the same event again — should be idempotent
        await saga.handle(event)
        assert len(saga.state.processed_event_ids) == 1

    @pytest.mark.anyio
    async def test_handle_ignores_events_in_terminal_state(
        self, saga_state: SagaState
    ) -> None:
        saga_state.status = SagaStatus.COMPLETED
        saga = OrderFulfillmentSaga(saga_state)
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        assert saga.state.is_event_processed(event.event_id) is False

    @pytest.mark.anyio
    async def test_handle_records_step(self, saga: OrderFulfillmentSaga) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        assert len(saga.state.step_history) == 1

    @pytest.mark.anyio
    async def test_handle_raises_for_unknown_event_type(
        self, saga: OrderFulfillmentSaga
    ) -> None:
        event = OrderFailed(order_id="ORD-1", reason="oops", correlation_id=uuid4())
        with pytest.raises(SagaHandlerNotFoundError, match="No handler registered"):
            await saga.handle(event)


class TestSagaCommandDispatch:
    """dispatch() and collect_commands()."""

    @pytest.mark.anyio
    async def test_dispatch_queues_command(self, saga: OrderFulfillmentSaga) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        commands = saga.collect_commands()
        assert len(commands) == 1
        assert isinstance(commands[0], ReserveItems)
        assert commands[0].order_id == "ORD-1"

    @pytest.mark.anyio
    async def test_collect_commands_clears_internal_list(
        self, saga: OrderFulfillmentSaga
    ) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        _ = saga.collect_commands()
        assert saga.collect_commands() == []

    @pytest.mark.anyio
    async def test_multiple_events_produce_multiple_commands(
        self, saga: OrderFulfillmentSaga
    ) -> None:
        cid = uuid4()
        event1 = OrderCreated(order_id="ORD-1", correlation_id=cid)
        event2 = ItemsReserved(order_id="ORD-1", item_count=5, correlation_id=cid)
        await saga.handle(event1)
        _ = saga.collect_commands()
        await saga.handle(event2)
        commands = saga.collect_commands()
        assert len(commands) == 1
        assert isinstance(commands[0], ConfirmOrder)


class TestSagaStepTracking:
    """Step transitions via on() step parameter."""

    @pytest.mark.anyio
    async def test_step_name_updates_on_event(self, saga: OrderFulfillmentSaga) -> None:
        assert saga.state.current_step == "init"
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        assert saga.state.current_step == "reserving"

    @pytest.mark.anyio
    async def test_step_transitions_through_lifecycle(
        self, saga: OrderFulfillmentSaga
    ) -> None:
        cid = uuid4()
        await saga.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        _ = saga.collect_commands()
        assert saga.state.current_step == "reserving"

        await saga.handle(
            ItemsReserved(order_id="ORD-1", item_count=5, correlation_id=cid)
        )
        _ = saga.collect_commands()
        assert saga.state.current_step == "confirming"


class TestSagaCompensation:
    """Compensation stack management."""

    @pytest.mark.anyio
    async def test_compensation_added_on_event(
        self, saga: OrderFulfillmentSaga
    ) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        assert len(saga.state.compensation_stack) == 1
        assert saga.state.compensation_stack[0].command_type == "CancelReservation"

    @pytest.mark.anyio
    async def test_execute_compensations_drains_stack(
        self, saga: OrderFulfillmentSaga
    ) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        _ = saga.collect_commands()  # clear forward commands
        assert len(saga.state.compensation_stack) == 1
        await saga.execute_compensations()
        assert len(saga.state.compensation_stack) == 0
        # execute_compensations() sets COMPENSATING and queues commands;
        # the manager is responsible for setting COMPENSATED/FAILED.
        assert saga.state.status == SagaStatus.COMPENSATING
        commands = saga.collect_commands()
        assert len(commands) == 1
        assert isinstance(commands[0], CancelReservation)

    @pytest.mark.anyio
    async def test_add_compensation_manual(self, saga: OrderFulfillmentSaga) -> None:
        cmd = CancelOrder(order_id="ORD-1")
        saga.add_compensation(cmd, description="Cancel entire order")
        assert len(saga.state.compensation_stack) == 1
        rec = saga.state.compensation_stack[0]
        assert rec.command_type == "CancelOrder"
        assert rec.description == "Cancel entire order"


class TestSagaLifecycle:
    """complete(), fail(), suspend(), resume()."""

    @pytest.mark.anyio
    async def test_complete(self, saga: OrderFulfillmentSaga) -> None:
        saga.complete()
        assert saga.state.status == SagaStatus.COMPLETED
        assert saga.state.completed_at is not None

    @pytest.mark.anyio
    async def test_fail_without_compensation(self, saga: OrderFulfillmentSaga) -> None:
        await saga.fail("something broke", compensate=False)
        assert saga.state.status == SagaStatus.FAILED
        assert saga.state.error == "something broke"
        assert saga.state.failed_at is not None

    @pytest.mark.anyio
    async def test_fail_with_compensation(self, saga: OrderFulfillmentSaga) -> None:
        # Push a compensation first
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        _ = saga.collect_commands()  # clear forward commands
        await saga.fail("payment failed")
        # fail() sets error/failed_at and calls execute_compensations()
        # which sets COMPENSATING.  The manager dispatches the queued
        # compensation commands and sets COMPENSATED/FAILED.
        assert saga.state.status == SagaStatus.COMPENSATING
        assert saga.state.error == "payment failed"
        assert saga.state.failed_at is not None
        commands = saga.collect_commands()
        assert len(commands) == 1
        assert isinstance(commands[0], CancelReservation)

    @pytest.mark.anyio
    async def test_suspend(self, saga: OrderFulfillmentSaga) -> None:
        from datetime import timedelta

        saga.suspend("waiting for approval", timeout=timedelta(hours=1))
        assert saga.state.status == SagaStatus.SUSPENDED
        assert saga.state.suspension_reason == "waiting for approval"
        assert saga.state.suspended_at is not None
        assert saga.state.timeout_at is not None

    @pytest.mark.anyio
    async def test_suspend_without_timeout(self, saga: OrderFulfillmentSaga) -> None:
        saga.suspend("waiting indefinitely")
        assert saga.state.status == SagaStatus.SUSPENDED
        assert saga.state.timeout_at is None

    @pytest.mark.anyio
    async def test_resume(self, saga: OrderFulfillmentSaga) -> None:
        saga.suspend("waiting")
        saga.resume()
        assert saga.state.status == SagaStatus.RUNNING
        assert saga.state.suspension_reason is None
        assert saga.state.suspended_at is None

    @pytest.mark.anyio
    async def test_resume_non_suspended_is_noop(
        self, saga: OrderFulfillmentSaga
    ) -> None:
        # saga starts in PENDING, not SUSPENDED
        saga.resume()
        assert saga.state.status == SagaStatus.PENDING


class TestSagaConfiguration:
    """on() validation and declarative mapping."""

    def test_on_with_both_handler_and_send_raises(self, saga_state: SagaState) -> None:
        saga = OrderFulfillmentSaga(saga_state)
        with pytest.raises(SagaConfigurationError, match="Cannot provide both"):
            saga.on(
                OrderCreated,
                handler=lambda e: None,
                send=lambda e: ReserveItems(order_id="x", item_count=1),
            )

    def test_on_with_neither_handler_nor_send_raises(
        self, saga_state: SagaState
    ) -> None:
        saga = OrderFulfillmentSaga(saga_state)
        with pytest.raises(SagaConfigurationError, match="Must provide either"):
            saga.on(OrderCreated)

    @pytest.mark.anyio
    async def test_handler_style_mapping(self, saga_state: SagaState) -> None:
        saga = HandlerStyleSaga(saga_state)
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        commands = saga.collect_commands()
        assert len(commands) == 1
        assert isinstance(commands[0], ReserveItems)


class TestSagaListenedEvents:
    """listened_events() class method."""

    def test_listened_events_from_subclass(self) -> None:
        assert OrderCreated in OrderFulfillmentSaga.listened_events()
        assert ItemsReserved in OrderFulfillmentSaga.listened_events()
        assert PaymentCompleted in OrderFulfillmentSaga.listened_events()

    def test_base_saga_has_no_events(self) -> None:
        assert Saga.listened_events() == []


# ── on_timeout tests ────────────────────────────────────────────────────


class TestSagaOnTimeout:
    """on_timeout() — default and overridden behaviour."""

    @pytest.mark.anyio
    async def test_on_timeout_calls_fail_by_default(
        self, saga: OrderFulfillmentSaga
    ) -> None:
        # Handle an event first so a compensation is registered
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        _ = saga.collect_commands()  # clear commands

        saga.suspend("waiting for payment")
        await saga.on_timeout()
        # fail() with compensation_stack non-empty → COMPENSATING
        assert saga.state.status == SagaStatus.COMPENSATING
        assert "Saga timed out while suspended" in saga.state.error
        assert "waiting for payment" in saga.state.error

    @pytest.mark.anyio
    async def test_on_timeout_custom_override(self) -> None:
        """Subclass overrides on_timeout for custom recovery."""

        class RetrySaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: ReserveItems(order_id=e.order_id, item_count=1),
                    step="reserving",
                )

            async def on_timeout(self) -> None:
                # Custom recovery: resume instead of fail
                self.resume()
                self.dispatch(ReserveItems(order_id="ORD-RETRY", item_count=1))

        state = SagaState(
            saga_type="RetrySaga",
            status=SagaStatus.SUSPENDED,
            suspension_reason="waiting for approval",
        )
        saga = RetrySaga(state)
        await saga.on_timeout()
        assert saga.state.status == SagaStatus.RUNNING
        commands = saga.collect_commands()
        assert len(commands) == 1
        assert isinstance(commands[0], ReserveItems)
        assert commands[0].order_id == "ORD-RETRY"
