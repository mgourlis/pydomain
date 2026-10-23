"""Tests for SagaManager — orchestration, happy paths,
compensation, recovery, suspension, multi-saga."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest

from pydomain.cqrs.command_bus import CommandBus
from pydomain.cqrs.commands import Command, EmptyCommandResult
from pydomain.cqrs.exceptions import CommandExecutionError
from pydomain.cqrs.saga.manager import SagaManager
from pydomain.cqrs.saga.registry import SagaRegistry
from pydomain.cqrs.saga.saga import Saga
from pydomain.cqrs.saga.state import CompensationRecord, SagaState, SagaStatus
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing import FakeUnitOfWork
from pydomain.testing.fake_saga_repository import FakeSagaRepository
from tests.saga.conftest import as_dispatcher

from .conftest import (
    ApprovalGranted,
    AuditSaga,
    CancelPayment,
    CancelReservation,
    CancelShipping,
    ConfirmOrder,
    DeliveryScheduled,
    FiveStepSaga,
    ItemsReserved,
    MultiDispatchSaga,
    OrderCreated,
    OrderShipped,
    PaymentProcessed,
    ProcessPayment,
    ReserveItems,
    ScheduleDelivery,
    SendNotification,
    ShipOrder,
    SuspendableSaga,
    TimeoutRetrySaga,
    TwoStepSaga,
    _noop_command_bus,
)

# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def saga_repo() -> FakeSagaRepository:
    return FakeSagaRepository()


@pytest.fixture
def command_bus() -> CommandBus:
    return _noop_command_bus()


@pytest.fixture
def two_step_registry() -> SagaRegistry:
    r = SagaRegistry()
    r.register_saga(TwoStepSaga)
    return r


@pytest.fixture
def two_step_manager(
    saga_repo: FakeSagaRepository,
    two_step_registry: SagaRegistry,
    command_bus: CommandBus,
) -> SagaManager:
    return SagaManager(
        repository=saga_repo,
        registry=two_step_registry,
        command_bus=as_dispatcher(command_bus),
    )


@pytest.fixture
def five_step_registry() -> SagaRegistry:
    r = SagaRegistry()
    r.register_saga(FiveStepSaga)
    return r


@pytest.fixture
def five_step_manager(
    saga_repo: FakeSagaRepository,
    five_step_registry: SagaRegistry,
    command_bus: CommandBus,
) -> SagaManager:
    return SagaManager(
        repository=saga_repo,
        registry=five_step_registry,
        command_bus=as_dispatcher(command_bus),
    )


# ═══════════════════════════════════════════════════════════════════════
# handle() — Event-Driven Choreography
# ═══════════════════════════════════════════════════════════════════════


class TestManagerHandle:
    """handle() routes events to registered sagas."""

    @pytest.mark.anyio
    async def test_handle_creates_new_saga(
        self,
        two_step_manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await two_step_manager.handle(event)
        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert state.status == SagaStatus.RUNNING

    @pytest.mark.anyio
    async def test_handle_no_correlation_id_is_noop(
        self,
        two_step_manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=None)
        await two_step_manager.handle(event)
        assert len(saga_repo._store) == 0

    @pytest.mark.anyio
    async def test_handle_no_registered_saga_is_noop(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        empty_registry = SagaRegistry()
        mgr = SagaManager(
            repository=saga_repo,
            registry=empty_registry,
            command_bus=as_dispatcher(command_bus),
        )
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await mgr.handle(event)
        assert len(saga_repo._store) == 0

    @pytest.mark.anyio
    async def test_handle_continues_existing_saga(
        self,
        two_step_manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        cid = uuid4()
        event1 = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await two_step_manager.handle(event1)

        state_before = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state_before is not None
        assert state_before.status == SagaStatus.RUNNING

        event2 = ItemsReserved(order_id="ORD-1", correlation_id=cid)
        await two_step_manager.handle(event2)

        state_after = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state_after is not None
        assert state_after.status == SagaStatus.COMPLETED
        assert len(state_after.step_history) == 2

    @pytest.mark.anyio
    async def test_handle_sets_causation_id_from_event(
        self,
        two_step_manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await two_step_manager.handle(event)
        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert state.causation_id == event.event_id

    @pytest.mark.anyio
    async def test_handle_dispatches_commands(
        self,
        two_step_manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        dispatched: list[Command[Any]] = []

        bus = CommandBus()

        async def capture(cmd: ReserveItems, uow: Any = None) -> EmptyCommandResult:
            dispatched.append(cmd)
            return EmptyCommandResult()

        bus.register(ReserveItems, capture, uow_factory=lambda: FakeUnitOfWork())

        mgr = SagaManager(
            repository=saga_repo,
            registry=two_step_manager.registry,
            command_bus=as_dispatcher(bus),
        )
        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event)
        assert len(dispatched) == 1
        assert isinstance(dispatched[0], ReserveItems)

    @pytest.mark.anyio
    async def test_handle_propagates_tracing_ids_on_commands(
        self,
        saga_repo: FakeSagaRepository,
        two_step_registry: SagaRegistry,
    ) -> None:
        dispatched: list[Command[Any]] = []

        bus = CommandBus()

        async def capture(cmd: ReserveItems, uow: Any = None) -> EmptyCommandResult:
            dispatched.append(cmd)
            return EmptyCommandResult()

        bus.register(ReserveItems, capture, uow_factory=lambda: FakeUnitOfWork())

        mgr = SagaManager(
            repository=saga_repo,
            registry=two_step_registry,
            command_bus=as_dispatcher(bus),
        )
        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event)
        assert dispatched[0].correlation_id == cid

    @pytest.mark.anyio
    async def test_handle_multiple_commands_dispatched(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        dispatched: list[Command[Any]] = []

        bus = CommandBus()

        async def capture(cmd: Command[Any], uow: Any = None) -> EmptyCommandResult:
            dispatched.append(cmd)
            return EmptyCommandResult()

        bus.register(ReserveItems, capture, uow_factory=lambda: FakeUnitOfWork())
        bus.register(SendNotification, capture, uow_factory=lambda: FakeUnitOfWork())

        registry = SagaRegistry()
        registry.register_saga(MultiDispatchSaga)
        mgr = SagaManager(
            repository=saga_repo, registry=registry, command_bus=as_dispatcher(bus)
        )

        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event)
        assert len(dispatched) == 2
        types = {type(c) for c in dispatched}
        assert ReserveItems in types


# ═══════════════════════════════════════════════════════════════════════
# start_saga() — Explicit Orchestration
# ═══════════════════════════════════════════════════════════════════════


class TestManagerStartSaga:
    """start_saga() — explicit orchestration entry point."""

    @pytest.mark.anyio
    async def test_start_saga_returns_saga_id(
        self,
        two_step_manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        saga_id = await two_step_manager.start_saga(TwoStepSaga, event)
        assert saga_id is not None
        assert await saga_repo.get_by_id(saga_id) is not None

    @pytest.mark.anyio
    async def test_start_saga_uses_provided_correlation_id(
        self,
        two_step_manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        explicit_cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        saga_id = await two_step_manager.start_saga(
            TwoStepSaga, event, correlation_id=explicit_cid
        )
        assert saga_id is not None
        state = await saga_repo.get_by_id(saga_id)
        assert state is not None
        assert state.correlation_id == explicit_cid

    @pytest.mark.anyio
    async def test_start_saga_generates_correlation_id_when_none(
        self,
        two_step_manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        event = OrderCreated(order_id="ORD-1")
        saga_id = await two_step_manager.start_saga(TwoStepSaga, event)
        assert saga_id is not None
        state = await saga_repo.get_by_id(saga_id)
        assert state is not None
        assert state.correlation_id is not None


# ═══════════════════════════════════════════════════════════════════════
# Terminal State Handling
# ═══════════════════════════════════════════════════════════════════════


class TestManagerTerminalState:
    """Terminal sagas are skipped when new events arrive."""

    @pytest.mark.anyio
    async def test_completed_saga_ignores_new_event(
        self,
        two_step_manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        cid = uuid4()
        await two_step_manager.handle(
            OrderCreated(order_id="ORD-1", correlation_id=cid)
        )
        await two_step_manager.handle(
            ItemsReserved(order_id="ORD-1", correlation_id=cid)
        )

        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPLETED

        # Send another event — saga should be skipped
        await two_step_manager.handle(
            OrderCreated(order_id="ORD-2", correlation_id=cid)
        )
        state_after = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state_after is not None
        assert len(state_after.step_history) == 2  # unchanged


# ═══════════════════════════════════════════════════════════════════════
# 5-Step Happy Path
# ═══════════════════════════════════════════════════════════════════════


class TestManagerFiveStepHappyPath:
    """Complete 5-step saga with no failures."""

    @pytest.mark.anyio
    async def test_full_five_step_saga(
        self,
        five_step_manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        cid = uuid4()
        event_classes = [
            OrderCreated,
            ItemsReserved,
            PaymentProcessed,
            OrderShipped,
            DeliveryScheduled,
        ]

        for i, event_cls in enumerate(event_classes):
            evt = event_cls(order_id="ORD-1", correlation_id=cid)  # type: ignore[call-arg]
            await five_step_manager.handle(evt)

        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPLETED
        assert len(state.step_history) == 5
        assert state.pending_commands == []
        assert state.compensation_stack == []

    @pytest.mark.anyio
    async def test_intermediate_states_are_running(
        self,
        five_step_manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        cid = uuid4()
        evt1 = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await five_step_manager.handle(evt1)

        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        assert state.status == SagaStatus.RUNNING
        assert len(state.compensation_stack) == 1


# ═══════════════════════════════════════════════════════════════════════
# Compensation Scenarios
# ═══════════════════════════════════════════════════════════════════════


class TestManagerCompensation:
    """Compensation flows through the SagaManager."""

    @pytest.mark.anyio
    async def test_fail_at_step2_compensates_only_step1(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """Step 2 handler raises before adding compensation —
        only step 1 compensates."""

        class Step2FailSaga(Saga[SagaState]):
            listens_to = [OrderCreated, ItemsReserved]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)
                self.on(ItemsReserved, handler=self._step2_fail)

            async def _step1(self, event: DomainEvent) -> None:
                self.state.current_step = "step1"
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(
                    CancelReservation(order_id="ORD-1"), "Cancel reservation"
                )

            async def _step2_fail(self, event: DomainEvent) -> None:
                self.state.current_step = "step2"
                self.dispatch(ProcessPayment(order_id="ORD-1"))
                raise RuntimeError("Payment gateway unreachable")

        registry = SagaRegistry()
        registry.register_saga(Step2FailSaga)
        mgr = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=as_dispatcher(command_bus),
        )

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "Step2FailSaga")
        assert state is not None
        assert len(state.compensation_stack) == 1

        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "Step2FailSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPENSATED
        assert state.compensation_stack == []
        assert len(state.failed_compensations) == 0
        assert state.error == "RuntimeError"

    @pytest.mark.anyio
    async def test_fail_at_step5_all_compensations_succeed(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """All 3 compensations succeed → COMPENSATED."""

        class Step5FailSaga(Saga[SagaState]):
            listens_to = [
                OrderCreated,
                ItemsReserved,
                PaymentProcessed,
                OrderShipped,
                DeliveryScheduled,
            ]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)
                self.on(ItemsReserved, handler=self._step2)
                self.on(PaymentProcessed, handler=self._step3)
                self.on(OrderShipped, handler=self._step4)
                self.on(DeliveryScheduled, handler=self._step5_fail)

            async def _step1(self, event: DomainEvent) -> None:
                self.state.current_step = "step1"
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(
                    CancelReservation(order_id="ORD-1"), "Cancel reservation"
                )

            async def _step2(self, event: DomainEvent) -> None:
                self.state.current_step = "step2"
                self.dispatch(ProcessPayment(order_id="ORD-1"))
                self.add_compensation(CancelPayment(order_id="ORD-1"), "Cancel payment")

            async def _step3(self, event: DomainEvent) -> None:
                self.state.current_step = "step3"
                self.dispatch(ShipOrder(order_id="ORD-1"))
                self.add_compensation(
                    CancelShipping(order_id="ORD-1"), "Cancel shipping"
                )

            async def _step4(self, event: DomainEvent) -> None:
                self.state.current_step = "step4"
                self.dispatch(ScheduleDelivery(order_id="ORD-1"))

            async def _step5_fail(self, event: DomainEvent) -> None:
                self.state.current_step = "step5"
                self.dispatch(ConfirmOrder(order_id="ORD-1"))
                raise RuntimeError("Confirmation service unavailable")

        registry = SagaRegistry()
        registry.register_saga(Step5FailSaga)
        mgr = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=as_dispatcher(command_bus),
        )

        cid = uuid4()
        for event_cls in [OrderCreated, ItemsReserved, PaymentProcessed, OrderShipped]:
            evt = event_cls(order_id="ORD-1", correlation_id=cid)  # type: ignore[call-arg]
            await mgr.handle(evt)

        state = await saga_repo.find_by_correlation_id(cid, "Step5FailSaga")
        assert state is not None
        assert len(state.compensation_stack) == 3

        await mgr.handle(DeliveryScheduled(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "Step5FailSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPENSATED
        assert len(state.failed_compensations) == 0
        assert state.compensation_stack == []
        assert state.error == "RuntimeError"

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-153 F-9 (B-186): a compensation stores CommandExecutionError",
    )
    @pytest.mark.anyio
    async def test_compensation_failure_marks_failed(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """One compensation dispatch fails → FAILED."""

        class SimpleFailSaga(Saga[SagaState]):
            listens_to = [OrderCreated, ItemsReserved]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)
                self.on(ItemsReserved, handler=self._step2_fail)

            async def _step1(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(
                    CancelReservation(order_id="ORD-1"), "Cancel reservation"
                )

            async def _step2_fail(self, event: DomainEvent) -> None:
                raise RuntimeError("Step 2 failed")

        bus = CommandBus()

        async def noop(cmd: Any, uow: Any = None) -> EmptyCommandResult:
            return EmptyCommandResult()

        async def fail_cancel(cmd: Any, uow: Any = None) -> EmptyCommandResult:
            raise RuntimeError("Cancel service down")

        bus.register(ReserveItems, noop, uow_factory=lambda: FakeUnitOfWork())
        bus.register(
            CancelReservation, fail_cancel, uow_factory=lambda: FakeUnitOfWork()
        )

        registry = SagaRegistry()
        registry.register_saga(SimpleFailSaga)
        mgr = SagaManager(
            repository=saga_repo, registry=registry, command_bus=as_dispatcher(bus)
        )

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "SimpleFailSaga")
        assert state is not None
        assert state.status == SagaStatus.FAILED
        assert len(state.failed_compensations) == 1
        assert state.failed_compensations[0]["command_type"] == "CancelReservation"
        # F-9 (B-186, DCE-153): the handler's own type.
        assert state.failed_compensations[0]["error"] == "RuntimeError"

    @pytest.mark.anyio
    async def test_no_compensations_fail_goes_to_failed(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """No compensation stack → FAILED directly."""

        class NoCompFailSaga(Saga[SagaState]):
            listens_to = [OrderCreated, ItemsReserved]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)
                self.on(ItemsReserved, handler=self._step2_fail)

            async def _step1(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                # No compensation registered

            async def _step2_fail(self, event: DomainEvent) -> None:
                raise RuntimeError("Failed without compensation")

        registry = SagaRegistry()
        registry.register_saga(NoCompFailSaga)
        mgr = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=as_dispatcher(command_bus),
        )

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "NoCompFailSaga")
        assert state is not None
        assert state.status == SagaStatus.FAILED
        assert state.error == "RuntimeError"


# ═══════════════════════════════════════════════════════════════════════
# Suspension Flows
# ═══════════════════════════════════════════════════════════════════════


class TestManagerSuspension:
    """Suspend/resume flows through the SagaManager."""

    @pytest.mark.anyio
    async def test_declarative_suspend_via_on(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        registry = SagaRegistry()
        registry.register_saga(SuspendableSaga)
        mgr = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=as_dispatcher(command_bus),
        )

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "SuspendableSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED
        assert state.suspension_reason == "Waiting for manager approval"
        assert state.timeout_at is not None

    @pytest.mark.anyio
    async def test_auto_resume_on_new_event(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """SUSPENDED saga auto-resumes when a matching event arrives."""
        registry = SagaRegistry()
        registry.register_saga(SuspendableSaga)
        mgr = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=as_dispatcher(command_bus),
        )

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        state = await saga_repo.find_by_correlation_id(cid, "SuspendableSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED

        # Send the approval event — should auto-resume and complete
        await mgr.handle(ApprovalGranted(order_id="ORD-1", correlation_id=cid))
        state = await saga_repo.find_by_correlation_id(cid, "SuspendableSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPLETED


# ═══════════════════════════════════════════════════════════════════════
# Timeout Processing
# ═══════════════════════════════════════════════════════════════════════


class TestManagerProcessTimeouts:
    """process_timeouts() — handle expired suspended sagas."""

    @pytest.mark.anyio
    async def test_process_timeouts_calls_on_timeout(
        self,
        two_step_manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await two_step_manager.handle(event)

        saga_state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert saga_state is not None
        saga_state.status = SagaStatus.SUSPENDED
        saga_state.suspension_reason = "waiting for payment"
        saga_state.suspended_at = datetime.now(UTC)
        saga_state.timeout_at = datetime.now(UTC) - timedelta(hours=1)
        await saga_repo.save(saga_state)

        await two_step_manager.process_timeouts()

        updated = await saga_repo.get_by_id(saga_state.id)
        assert updated is not None
        assert updated.status in (
            SagaStatus.COMPENSATING,
            SagaStatus.FAILED,
            SagaStatus.COMPENSATED,
        )
        assert "timed out" in (updated.error or "").lower()

    @pytest.mark.anyio
    async def test_process_timeouts_custom_recovery(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        dispatched_commands: list[Command[Any]] = []

        bus = CommandBus()

        async def capture(cmd: ReserveItems, uow: Any = None) -> EmptyCommandResult:
            dispatched_commands.append(cmd)
            return EmptyCommandResult()

        bus.register(ReserveItems, capture, uow_factory=lambda: FakeUnitOfWork())

        registry = SagaRegistry()
        registry.register_saga(TimeoutRetrySaga)
        manager = SagaManager(
            repository=saga_repo, registry=registry, command_bus=as_dispatcher(bus)
        )

        state = SagaState(
            saga_type="TimeoutRetrySaga",
            status=SagaStatus.SUSPENDED,
            suspension_reason="waiting",
            timeout_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        await saga_repo.save(state)

        await manager.process_timeouts()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.status == SagaStatus.RUNNING
        assert len(dispatched_commands) == 1
        assert dispatched_commands[0].order_id == "ORD-RETRY"

    @pytest.mark.anyio
    async def test_process_timeouts_on_timeout_raises_force_fails(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        class BrokenTimeoutSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: ReserveItems(order_id=e.order_id),
                    step="reserving",
                )

            async def on_timeout(self) -> None:
                raise RuntimeError("Timeout handler crashed")

        registry = SagaRegistry()
        registry.register_saga(BrokenTimeoutSaga)
        manager = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=as_dispatcher(command_bus),
        )

        state = SagaState(
            saga_type="BrokenTimeoutSaga",
            status=SagaStatus.SUSPENDED,
            timeout_at=datetime.now(UTC) - timedelta(hours=1),
        )
        await saga_repo.save(state)

        await manager.process_timeouts()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.status == SagaStatus.FAILED
        assert "Timeout handler failed" in (updated.error or "")

    @pytest.mark.anyio
    async def test_process_timeouts_force_fails_if_still_suspended(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """on_timeout that doesn't resolve → force-fail."""

        class NoOpTimeoutSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: ReserveItems(order_id=e.order_id),
                    step="reserving",
                )

            async def on_timeout(self) -> None:
                pass  # Does nothing — doesn't fail, resume, or complete

        registry = SagaRegistry()
        registry.register_saga(NoOpTimeoutSaga)
        manager = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=as_dispatcher(command_bus),
        )

        state = SagaState(
            saga_type="NoOpTimeoutSaga",
            status=SagaStatus.SUSPENDED,
            timeout_at=datetime.now(UTC) - timedelta(hours=1),
        )
        await saga_repo.save(state)

        await manager.process_timeouts()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.status == SagaStatus.FAILED
        assert "did not resolve" in (updated.error or "").lower()


# ═══════════════════════════════════════════════════════════════════════
# Recovery Flows
# ═══════════════════════════════════════════════════════════════════════


class TestManagerRecovery:
    """recover_pending_sagas() — re-dispatch stalled commands."""

    @pytest.mark.anyio
    async def test_recover_pending_sagas_redispatches_commands(
        self,
        two_step_manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        cid = uuid4()
        state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=cid,
            status=SagaStatus.RUNNING,
            pending_commands=[
                {
                    "command_type": "ReserveItems",
                    "module_name": ReserveItems.__module__,
                    "data": {"order_id": "ORD-1", "item_count": 5},
                    "dispatched": False,
                },
            ],
        )
        await saga_repo.save(state)

        await two_step_manager.recover_pending_sagas()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.pending_commands == []
        assert updated.retry_count == 0

    @pytest.mark.anyio
    async def test_recover_pending_sagas_fails_on_max_retries(
        self,
        saga_repo: FakeSagaRepository,
        two_step_registry: SagaRegistry,
        command_bus: CommandBus,
    ) -> None:
        manager = SagaManager(
            repository=saga_repo,
            registry=two_step_registry,
            command_bus=as_dispatcher(command_bus),
        )
        state = SagaState(
            saga_type="TwoStepSaga",
            status=SagaStatus.RUNNING,
            retry_count=4,  # past max_retries=3: exhausted (ADR-077, B-85)
            max_retries=3,
            pending_commands=[
                {
                    "command_type": "ReserveItems",
                    "module_name": ReserveItems.__module__,
                    "data": {"order_id": "ORD-1", "item_count": 5},
                    "dispatched": False,
                },
            ],
        )
        await saga_repo.save(state)

        await manager.recover_pending_sagas()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.status == SagaStatus.FAILED
        assert "Max retries exceeded" in updated.error

    @pytest.mark.anyio
    async def test_recover_pending_sagas_resets_retry_count_on_success(
        self,
        two_step_manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        state = SagaState(
            saga_type="TwoStepSaga",
            status=SagaStatus.RUNNING,
            retry_count=2,
            pending_commands=[
                {
                    "command_type": "ReserveItems",
                    "module_name": ReserveItems.__module__,
                    "data": {"order_id": "ORD-1", "item_count": 5},
                    "dispatched": False,
                },
            ],
        )
        await saga_repo.save(state)

        await two_step_manager.recover_pending_sagas()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.retry_count == 0
        assert updated.pending_commands == []

    @pytest.mark.anyio
    async def test_recover_pending_sagas_skips_unknown_saga_type(
        self,
        two_step_manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        state = SagaState(
            saga_type="NonExistentSaga",
            status=SagaStatus.RUNNING,
            pending_commands=[
                {
                    "command_type": "ReserveItems",
                    "module_name": ReserveItems.__module__,
                    "data": {"order_id": "ORD-1", "item_count": 5},
                    "dispatched": False,
                },
            ],
        )
        await saga_repo.save(state)

        await two_step_manager.recover_pending_sagas()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.pending_commands  # unchanged

    @pytest.mark.anyio
    async def test_forward_command_dispatch_failure_suspends(
        self,
        saga_repo: FakeSagaRepository,
        two_step_registry: SagaRegistry,
    ) -> None:
        """Dispatch failure suspends the saga for recovery."""
        bus = CommandBus()

        async def fail_dispatch(cmd: Any, uow: Any = None) -> EmptyCommandResult:
            raise RuntimeError("Command bus down")

        bus.register(ReserveItems, fail_dispatch, uow_factory=lambda: FakeUnitOfWork())

        mgr = SagaManager(
            repository=saga_repo,
            registry=two_step_registry,
            command_bus=as_dispatcher(bus),
        )

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED
        assert state.retry_count == 1
        assert "Dispatch failed" in (state.suspension_reason or "")


# ═══════════════════════════════════════════════════════════════════════
# bind_to()
# ═══════════════════════════════════════════════════════════════════════


class TestManagerBindTo:
    """bind_to() — auto-register manager as event handler."""

    def test_bind_to_registers_all_event_types(self) -> None:
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        saga_repo = FakeSagaRepository()
        command_bus = CommandBus()
        manager = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=as_dispatcher(command_bus),
        )

        registered: dict[type, list] = {}

        class FakeDispatcher:
            def register_event(
                self, event_type: type, handler: object, **kwargs: object
            ) -> None:
                registered.setdefault(event_type, []).append(handler)

        manager.bind_to(FakeDispatcher())

        assert OrderCreated in registered
        assert ItemsReserved in registered
        assert registered[OrderCreated] == [manager.handle]

    def test_bind_to_empty_registry_is_noop(self) -> None:
        registry = SagaRegistry()
        saga_repo = FakeSagaRepository()
        command_bus = CommandBus()
        manager = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=as_dispatcher(command_bus),
        )

        registered: dict[type, list] = {}

        class FakeDispatcher:
            def register_event(
                self, event_type: type, handler: object, **kwargs: object
            ) -> None:
                registered.setdefault(event_type, []).append(handler)

        manager.bind_to(FakeDispatcher())
        assert registered == {}

    def test_bind_to_multiple_sagas_shares_handler(self) -> None:
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        registry.register_saga(AuditSaga)

        saga_repo = FakeSagaRepository()
        command_bus = CommandBus()
        manager = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=as_dispatcher(command_bus),
        )

        registered: dict[type, list] = {}

        class FakeDispatcher:
            def register_event(
                self, event_type: type, handler: object, **kwargs: object
            ) -> None:
                registered.setdefault(event_type, []).append(handler)

        manager.bind_to(FakeDispatcher())
        # OrderCreated registered once even though two sagas listen to it
        assert registered[OrderCreated] == [manager.handle]


# ═══════════════════════════════════════════════════════════════════════
# Multi-Saga Flows
# ═══════════════════════════════════════════════════════════════════════


class TestManagerMultiSaga:
    """Multiple sagas for same event — independent state."""

    @pytest.mark.anyio
    async def test_two_sagas_for_same_event(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        registry.register_saga(AuditSaga)
        mgr = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=as_dispatcher(command_bus),
        )

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        two_step_state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        audit_state = await saga_repo.find_by_correlation_id(cid, "AuditSaga")

        assert two_step_state is not None
        assert audit_state is not None
        assert two_step_state.status == SagaStatus.RUNNING
        assert (
            audit_state.status == SagaStatus.COMPLETED
        )  # AuditSaga completes immediately
        assert two_step_state.id != audit_state.id

    @pytest.mark.anyio
    async def test_sagas_have_independent_lifecycle(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        registry.register_saga(AuditSaga)
        mgr = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=as_dispatcher(command_bus),
        )

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        # AuditSaga is already COMPLETED, TwoStepSaga is RUNNING
        audit_state = await saga_repo.find_by_correlation_id(cid, "AuditSaga")
        assert audit_state is not None
        assert audit_state.status == SagaStatus.COMPLETED

        # Continue TwoStepSaga
        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))
        two_step_state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert two_step_state is not None
        assert two_step_state.status == SagaStatus.COMPLETED


# ═══════════════════════════════════════════════════════════════════════
# Tracing ID Propagation
# ═══════════════════════════════════════════════════════════════════════


class TestManagerTracing:
    """Correlation ID and causation ID propagation through dispatch paths."""

    @pytest.mark.anyio
    async def test_forward_commands_carry_correlation_id(
        self,
        saga_repo: FakeSagaRepository,
        two_step_registry: SagaRegistry,
    ) -> None:
        dispatched: list[Command[Any]] = []
        bus = CommandBus()

        async def capture(cmd: Command[Any], uow: Any = None) -> EmptyCommandResult:
            dispatched.append(cmd)
            return EmptyCommandResult()

        bus.register(ReserveItems, capture, uow_factory=lambda: FakeUnitOfWork())

        mgr = SagaManager(
            repository=saga_repo,
            registry=two_step_registry,
            command_bus=as_dispatcher(bus),
        )
        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        assert dispatched[0].correlation_id == cid

    @pytest.mark.anyio
    async def test_compensation_commands_carry_tracing_ids(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """Compensation commands get correlation_id and causation_id."""
        dispatched: list[Command[Any]] = []
        bus = CommandBus()

        async def capture(cmd: Command[Any], uow: Any = None) -> EmptyCommandResult:
            dispatched.append(cmd)
            return EmptyCommandResult()

        bus.register(ReserveItems, capture, uow_factory=lambda: FakeUnitOfWork())
        bus.register(CancelReservation, capture, uow_factory=lambda: FakeUnitOfWork())

        class SimpleFailSaga(Saga[SagaState]):
            listens_to = [OrderCreated, ItemsReserved]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)
                self.on(ItemsReserved, handler=self._step2_fail)

            async def _step1(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")

            async def _step2_fail(self, event: DomainEvent) -> None:
                raise RuntimeError("Fail")

        registry = SagaRegistry()
        registry.register_saga(SimpleFailSaga)
        mgr = SagaManager(
            repository=saga_repo, registry=registry, command_bus=as_dispatcher(bus)
        )

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        # Compensation command should have correlation_id
        comp_cmds = [c for c in dispatched if isinstance(c, CancelReservation)]
        assert len(comp_cmds) == 1
        assert comp_cmds[0].correlation_id == cid


# ═══════════════════════════════════════════════════════════════════════
# Stored errors name the exception type only (ADR-071)
# ═══════════════════════════════════════════════════════════════════════


class _RefundFailsSaga(Saga[SagaState]):
    listens_to = [OrderCreated, ItemsReserved]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(OrderCreated, handler=self._reserve)
        self.on(ItemsReserved, handler=self._crash)

    async def _reserve(self, event: DomainEvent) -> None:
        self.dispatch(ReserveItems(order_id="ORD-1"))
        self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")

    async def _crash(self, event: DomainEvent) -> None:
        raise RuntimeError("Step 2 failed for ORD-1")

    async def on_timeout(self) -> None:
        raise LookupError("reminder for ORD-1 not found")


class _RawCancelRefusingBus:
    """A dispatcher that raises the handler's own exception (ADR-077 point 6)."""

    async def dispatch(self, command: Command[Any]) -> Any:
        if isinstance(command, CancelReservation):
            raise ConnectionError("Cancel service down for ORD-1")
        return EmptyCommandResult()


def _cancel_refusing_message_bus() -> Any:
    bus = CommandBus()

    async def noop(cmd: Any, uow: Any = None) -> EmptyCommandResult:
        return EmptyCommandResult()

    async def fail_cancel(cmd: Any, uow: Any = None) -> EmptyCommandResult:
        raise ConnectionError("Cancel service down for ORD-1")

    bus.register(ReserveItems, noop, uow_factory=lambda: FakeUnitOfWork())
    bus.register(CancelReservation, fail_cancel, uow_factory=lambda: FakeUnitOfWork())
    return as_dispatcher(bus)


@pytest.mark.parametrize(
    "dispatcher",
    [
        pytest.param(
            _cancel_refusing_message_bus,
            id="message-bus",
            marks=pytest.mark.xfail(
                strict=True,
                raises=AssertionError,
                reason="DCE-153 F-9 (B-186): a compensation stores "
                "CommandExecutionError through a bus",
            ),
        ),
        pytest.param(_RawCancelRefusingBus, id="raw-dispatcher"),
    ],
)
@pytest.mark.anyio
async def test_failed_compensation_stores_its_handler_exception_type(
    saga_repo: FakeSagaRepository, dispatcher: Any
) -> None:
    """B-186: a failed compensation stores its handler's own type, through a
    bus as through a dispatcher that raises it, like a failed forward command."""
    registry = SagaRegistry()
    registry.register_saga(_RefundFailsSaga)
    mgr = SagaManager(
        repository=saga_repo,
        registry=registry,
        command_bus=cast(CommandBus, dispatcher()),
    )
    cid = uuid4()

    await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
    await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

    state = await saga_repo.find_by_correlation_id(cid, "_RefundFailsSaga")
    assert state is not None
    [failure] = state.failed_compensations
    assert failure["command_type"] == "CancelReservation"
    assert failure["error"] == "ConnectionError"


class TestSagaManagerStoredErrors:
    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-153 F-9 (B-186): a compensation stores CommandExecutionError",
    )
    @pytest.mark.anyio
    async def test_handler_and_compensation_errors_are_stored_by_type(
        self, saga_repo: FakeSagaRepository, caplog: pytest.LogCaptureFixture
    ) -> None:
        bus = CommandBus()

        async def noop(cmd: Any, uow: Any = None) -> EmptyCommandResult:
            return EmptyCommandResult()

        async def fail_cancel(cmd: Any, uow: Any = None) -> EmptyCommandResult:
            raise RuntimeError("Cancel service down for ORD-1")

        bus.register(ReserveItems, noop, uow_factory=lambda: FakeUnitOfWork())
        bus.register(
            CancelReservation, fail_cancel, uow_factory=lambda: FakeUnitOfWork()
        )
        registry = SagaRegistry()
        registry.register_saga(_RefundFailsSaga)
        mgr = SagaManager(
            repository=saga_repo, registry=registry, command_bus=as_dispatcher(bus)
        )
        cid = uuid4()
        caplog.set_level(logging.ERROR, logger="pydomain.saga")

        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "_RefundFailsSaga")
        assert state is not None
        assert state.status == SagaStatus.FAILED
        assert state.error == "RuntimeError"
        [failure] = state.failed_compensations
        # F-9 (B-186, DCE-153): the handler's own type.
        assert failure["error"] == "RuntimeError"
        assert failure["command_type"] == "CancelReservation"
        assert failure["module_name"] == CancelReservation.__module__
        # The line describes the handler's own exception and carries a
        # value-free stack (DCE-153, ADR-081 points 4 and 6, B-186, B-202).
        assert (
            f"Compensation command CancelReservation failed for saga {state.id}: "
            "RuntimeError"
        ) in [r.getMessage().split("\n")[0] for r in caplog.records]

    @pytest.mark.anyio
    async def test_timeout_handler_error_is_stored_by_type(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        registry = SagaRegistry()
        registry.register_saga(_RefundFailsSaga)
        mgr = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=as_dispatcher(command_bus),
        )
        state = SagaState(
            saga_type="_RefundFailsSaga",
            status=SagaStatus.SUSPENDED,
            timeout_at=datetime.now(UTC) - timedelta(hours=1),
        )
        await saga_repo.save(state)

        await mgr.process_timeouts()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.error == "Timeout handler failed: LookupError"


class TestProcessTimeoutsSkipsAndForceFails:
    """process_timeouts: unknown types, the force-fail reason, the batch limit."""

    @staticmethod
    def _expired(saga_type: str, **fields: Any) -> SagaState:
        return SagaState(
            saga_type=saga_type,
            status=SagaStatus.SUSPENDED,
            timeout_at=datetime.now(UTC) - timedelta(hours=1),
            **fields,
        )

    @pytest.mark.anyio
    async def test_unknown_saga_type_is_skipped_with_a_warning(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        registry = SagaRegistry()
        registry.register_saga(_RefundFailsSaga)
        mgr = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=as_dispatcher(command_bus),
        )
        unknown = self._expired("RetiredSaga")
        known = self._expired("_RefundFailsSaga")
        await saga_repo.save(unknown)
        await saga_repo.save(known)
        caplog.set_level(logging.WARNING, logger="pydomain.saga")

        await mgr.process_timeouts()

        assert "Unknown saga type RetiredSaga — skipping timeout" in [
            r.getMessage() for r in caplog.records
        ]
        skipped = await saga_repo.get_by_id(unknown.id)
        handled = await saga_repo.get_by_id(known.id)
        assert skipped is not None
        assert skipped.status == SagaStatus.SUSPENDED
        assert handled is not None
        assert handled.status == SagaStatus.FAILED

    @pytest.mark.anyio
    async def test_unresolved_suspension_fails_without_compensating(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        class IdleTimeoutSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            async def on_timeout(self) -> None:
                pass

        registry = SagaRegistry()
        registry.register_saga(IdleTimeoutSaga)
        mgr = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=as_dispatcher(command_bus),
        )
        state = self._expired(
            "IdleTimeoutSaga",
            compensation_stack=[
                CompensationRecord(
                    command_type="CancelReservation",
                    data={"order_id": "ORD-1"},
                    module_name=CancelReservation.__module__,
                )
            ],
        )
        await saga_repo.save(state)

        await mgr.process_timeouts()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.status == SagaStatus.FAILED
        assert updated.error == "Timeout handler did not resolve suspension"
        assert len(updated.compensation_stack) == 1

    @pytest.mark.anyio
    async def test_default_limit_handles_ten_sagas_per_call(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        registry = SagaRegistry()
        registry.register_saga(_RefundFailsSaga)
        mgr = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=as_dispatcher(command_bus),
        )
        for _ in range(11):
            await saga_repo.save(self._expired("_RefundFailsSaga"))

        await mgr.process_timeouts()

        assert len(await saga_repo.find_expired_suspended_sagas(limit=100)) == 1


class _RaisingBus:
    """A command bus double whose dispatch raises the given exception."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def dispatch(self, command: Command[Any]) -> Any:
        raise self._error


def _chained(error: Exception, cause: Exception) -> Exception:
    error.__cause__ = cause
    return error


class TestDispatchFailureSuspensionReason:
    """The suspension reason names the failure's type only (ADR-071, B-42)."""

    @pytest.mark.parametrize(
        ("error", "reason"),
        [
            (RuntimeError("ORD-1 refused"), "Dispatch failed: RuntimeError"),
            (
                _chained(RuntimeError("ORD-1 refused"), KeyError("ORD-1")),
                "Dispatch failed: RuntimeError",
            ),
            (
                CommandExecutionError(ReserveItems(order_id="ORD-1")),
                "Dispatch failed: CommandExecutionError",
            ),
            (
                _chained(
                    CommandExecutionError(ReserveItems(order_id="ORD-1")),
                    LookupError("ORD-1"),
                ),
                "Dispatch failed: LookupError",
            ),
        ],
    )
    @pytest.mark.anyio
    async def test_suspension_reason_describes_the_handler_failure(
        self, saga_repo: FakeSagaRepository, error: Exception, reason: str
    ) -> None:
        registry = SagaRegistry()
        registry.register_saga(_RefundFailsSaga)
        mgr = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=as_dispatcher(cast(CommandBus, _RaisingBus(error))),
        )
        cid = uuid4()

        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "_RefundFailsSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED
        assert state.suspension_reason == reason
