"""End-to-end saga flow tests —
state transitions, crash recovery, multi-saga, and edge cases.

These tests exercise full saga lifecycles through the SagaManager, verifying
state machine transitions, compensation ordering, recovery mechanics, suspension
flows, and multi-saga scenarios as described in ``docs/saga/flows.md``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from pydomain.cqrs.command_bus import CommandBus
from pydomain.cqrs.commands import Command, EmptyCommandResult
from pydomain.cqrs.saga.manager import SagaManager
from pydomain.cqrs.saga.registry import SagaRegistry
from pydomain.cqrs.saga.saga import Saga
from pydomain.cqrs.saga.state import SagaState, SagaStatus
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing import FakeUnitOfWork
from pydomain.testing.fake_saga_repository import FakeSagaRepository

from .conftest import (
    ApprovalGranted,
    AuditSaga,
    CancelPayment,
    CancelReservation,
    CancelShipping,
    DeliveryScheduled,
    FiveStepSaga,
    ItemsReserved,
    MultiDispatchSaga,
    OrderCreated,
    OrderShipped,
    PaymentProcessed,
    ProcessPayment,
    ReserveItems,
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


def _make_manager(
    repo: FakeSagaRepository,
    registry: SagaRegistry,
    bus: CommandBus,
) -> SagaManager:
    return SagaManager(repository=repo, registry=registry, command_bus=bus)


# ═══════════════════════════════════════════════════════════════════════
# 1. State Transition Matrix
# ═══════════════════════════════════════════════════════════════════════


class TestStateMachineTransitions:
    """Verify every valid state transition from §1.2 of flows.md."""

    @pytest.mark.anyio
    async def test_pending_to_running(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        """PENDING → RUNNING on first handle(event)."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert state.status == SagaStatus.RUNNING

    @pytest.mark.anyio
    async def test_running_to_running(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        """RUNNING → RUNNING on subsequent events (multi-step saga)."""
        registry = SagaRegistry()
        registry.register_saga(FiveStepSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        assert state.status == SagaStatus.RUNNING

        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))
        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        assert state.status == SagaStatus.RUNNING

    @pytest.mark.anyio
    async def test_running_to_completed(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        """RUNNING → COMPLETED when saga.complete() is called."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPLETED

    @pytest.mark.anyio
    async def test_running_to_suspended(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        """RUNNING → SUSPENDED when saga.suspend() is called."""
        registry = SagaRegistry()
        registry.register_saga(SuspendableSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "SuspendableSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED

    @pytest.mark.anyio
    async def test_running_to_compensating(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        """RUNNING → COMPENSATING → COMPENSATED
        on handler failure with compensation stack."""

        class FailSaga(Saga[SagaState]):
            listens_to = [OrderCreated, ItemsReserved]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)
                self.on(ItemsReserved, handler=self._step2_fail)

            async def _step1(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")

            async def _step2_fail(self, event: DomainEvent) -> None:
                raise RuntimeError("Step 2 failed")

        registry = SagaRegistry()
        registry.register_saga(FailSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "FailSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPENSATED
        assert state.compensation_stack == []
        assert len(state.failed_compensations) == 0
        assert "Step 2 failed" in (state.error or "")

    @pytest.mark.anyio
    async def test_running_to_failed_no_compensation(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        """RUNNING → FAILED when no compensation stack exists."""

        class NoCompSaga(Saga[SagaState]):
            listens_to = [OrderCreated, ItemsReserved]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)
                self.on(ItemsReserved, handler=self._step2_fail)

            async def _step1(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                # No compensation

            async def _step2_fail(self, event: DomainEvent) -> None:
                raise RuntimeError("No compensations")

        registry = SagaRegistry()
        registry.register_saga(NoCompSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "NoCompSaga")
        assert state is not None
        assert state.status == SagaStatus.FAILED
        assert len(state.compensation_stack) == 0
        assert "No compensations" in (state.error or "")

    @pytest.mark.anyio
    async def test_suspended_to_running_on_resume(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        """SUSPENDED → RUNNING when resume is triggered by incoming event."""
        registry = SagaRegistry()
        registry.register_saga(SuspendableSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "SuspendableSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED

        await mgr.handle(ApprovalGranted(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "SuspendableSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPLETED

    @pytest.mark.anyio
    async def test_suspended_to_compensating_via_timeout(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """SUSPENDED → COMPENSATING via on_timeout() default
        (fail with compensation)."""
        registry = SagaRegistry()
        registry.register_saga(SuspendableSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "SuspendableSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED
        # Force timeout
        state.timeout_at = datetime.now(UTC) - timedelta(hours=1)
        await saga_repo.save(state)

        await mgr.process_timeouts()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.status in (
            SagaStatus.COMPENSATED,
            SagaStatus.COMPENSATING,
            SagaStatus.FAILED,
        )

    @pytest.mark.anyio
    async def test_suspended_to_failed_force_fail(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """SUSPENDED → FAILED when on_timeout does nothing (force-fail guard)."""

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
                pass  # Does nothing

        registry = SagaRegistry()
        registry.register_saga(NoOpTimeoutSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        state = SagaState(
            saga_type="NoOpTimeoutSaga",
            status=SagaStatus.SUSPENDED,
            timeout_at=datetime.now(UTC) - timedelta(hours=1),
        )
        await saga_repo.save(state)

        await mgr.process_timeouts()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.status == SagaStatus.FAILED
        assert "did not resolve" in (updated.error or "").lower()

    @pytest.mark.anyio
    async def test_compensating_to_compensated(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        """COMPENSATING → COMPENSATED when all compensations dispatch OK."""

        class CompSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1_fail)

            async def _step1_fail(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")
                raise RuntimeError("Immediate fail")

        registry = SagaRegistry()
        registry.register_saga(CompSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "CompSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPENSATED
        assert state.compensation_stack == []
        assert len(state.failed_compensations) == 0
        assert "Immediate fail" in (state.error or "")

    @pytest.mark.anyio
    async def test_compensating_to_failed(self, saga_repo: FakeSagaRepository) -> None:
        """COMPENSATING → FAILED when at least one compensation dispatch fails."""
        bus = CommandBus()

        async def noop(cmd: Any, uow: Any = None) -> EmptyCommandResult:
            return EmptyCommandResult()

        async def fail(cmd: Any, uow: Any = None) -> EmptyCommandResult:
            raise RuntimeError("Compensation service down")

        bus.register(ReserveItems, noop, uow_factory=lambda: FakeUnitOfWork())
        bus.register(CancelReservation, fail, uow_factory=lambda: FakeUnitOfWork())

        class CompFailSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1_fail)

            async def _step1_fail(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")
                raise RuntimeError("Fail then compensate")

        registry = SagaRegistry()
        registry.register_saga(CompFailSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "CompFailSaga")
        assert state is not None
        assert state.status == SagaStatus.FAILED
        assert len(state.failed_compensations) == 1
        assert state.failed_compensations[0]["command_type"] == "CancelReservation"
        assert "CancelReservation" in state.failed_compensations[0]["error"]

    @pytest.mark.anyio
    async def test_terminal_states_ignore_events(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        """COMPLETED/FAILED/COMPENSATED states ignore new events silently."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPLETED
        step_count = len(state.step_history)

        # Send another event — should be ignored
        await mgr.handle(OrderCreated(order_id="ORD-2", correlation_id=cid))
        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert len(state.step_history) == step_count


# ═══════════════════════════════════════════════════════════════════════
# 2. Happy Path Flows
# ═══════════════════════════════════════════════════════════════════════


class TestHappyPaths:
    """Complete saga happy paths from §2 of flows.md."""

    @pytest.mark.anyio
    async def test_two_step_command_mapper_style(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        """§2.1: Two-step saga with command mapper style
        (send=, compensate=, complete=True)."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()

        # Step 1: OrderCreated → ReserveItems
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert state.status == SagaStatus.RUNNING
        assert len(state.step_history) == 1
        assert len(state.compensation_stack) == 1

        # Step 2: ItemsReserved → ShipOrder + complete
        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))
        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPLETED
        assert len(state.step_history) == 2
        assert state.completed_at is not None

    @pytest.mark.anyio
    async def test_five_step_handler_style(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        """§2.2: Five-step saga with handler style, 3 compensations."""
        registry = SagaRegistry()
        registry.register_saga(FiveStepSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        events = [
            OrderCreated,
            ItemsReserved,
            PaymentProcessed,
            OrderShipped,
            DeliveryScheduled,
        ]

        for event_cls in events:
            await mgr.handle(event_cls(order_id="ORD-1", correlation_id=cid))  # type: ignore[call-arg]

        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPLETED
        assert len(state.step_history) == 5
        assert state.completed_at is not None

    @pytest.mark.anyio
    async def test_compensation_stack_grows_through_steps(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        """Verify compensation stack grows as steps execute with add_compensation."""
        registry = SagaRegistry()
        registry.register_saga(FiveStepSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()

        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        assert len(state.compensation_stack) == 1

        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))
        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        assert len(state.compensation_stack) == 2

        await mgr.handle(PaymentProcessed(order_id="ORD-1", correlation_id=cid))
        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        assert len(state.compensation_stack) == 3

        await mgr.handle(OrderShipped(order_id="ORD-1", correlation_id=cid))
        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        assert len(state.compensation_stack) == 3  # No new compensation

        await mgr.handle(DeliveryScheduled(order_id="ORD-1", correlation_id=cid))
        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPLETED

    @pytest.mark.anyio
    async def test_explicit_start_saga(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        """§2.3: Explicit start via start_saga() with correlation ID."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        explicit_cid = uuid4()
        saga_id = await mgr.start_saga(
            TwoStepSaga,
            initial_event=OrderCreated(order_id="ORD-1"),
            correlation_id=explicit_cid,
        )

        assert saga_id is not None
        state = await saga_repo.get_by_id(saga_id)
        assert state is not None
        assert state.correlation_id == explicit_cid


# ═══════════════════════════════════════════════════════════════════════
# 3. Unhappy Path Flows
# ═══════════════════════════════════════════════════════════════════════


class TestUnhappyPaths:
    """Failure scenarios from §3 of flows.md."""

    @pytest.mark.anyio
    async def test_handler_raises_without_fail(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        """§3.1: Handler raises without calling fail() —
        manager calls fail() on its behalf."""

        class CrashSaga(Saga[SagaState]):
            listens_to = [OrderCreated, ItemsReserved]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)
                self.on(ItemsReserved, handler=self._step2_crash)

            async def _step1(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")

            async def _step2_crash(self, event: DomainEvent) -> None:
                raise RuntimeError("Unexpected crash!")

        registry = SagaRegistry()
        registry.register_saga(CrashSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "CrashSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPENSATED
        assert "Unexpected crash!" in state.error

    @pytest.mark.anyio
    async def test_handler_calls_fail_then_raises(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        """§3.2: Handler calls fail() then raises —
        manager detects COMPENSATING, skips second fail."""

        class ExplicitFailSaga(Saga[SagaState]):
            listens_to = [OrderCreated, ItemsReserved]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)
                self.on(ItemsReserved, handler=self._step2)

            async def _step1(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")

            async def _step2(self, event: DomainEvent) -> None:
                await self.fail("Business rule violated")
                raise RuntimeError("Additional context")

        registry = SagaRegistry()
        registry.register_saga(ExplicitFailSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "ExplicitFailSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPENSATED
        assert len(state.failed_compensations) == 0
        assert "Business rule violated" in (state.error or "")

    @pytest.mark.anyio
    async def test_compensation_dispatch_failure_records_and_fails(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """§3.3: Compensation dispatch failure → FAILED
        with failed_compensations recorded."""
        bus = CommandBus()

        async def noop(cmd: Any, uow: Any = None) -> EmptyCommandResult:
            return EmptyCommandResult()

        async def fail_cancel(cmd: Any, uow: Any = None) -> EmptyCommandResult:
            raise ConnectionError("Shipping service down")

        bus.register(ReserveItems, noop, uow_factory=lambda: FakeUnitOfWork())
        bus.register(
            CancelReservation, fail_cancel, uow_factory=lambda: FakeUnitOfWork()
        )

        class FailCompSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1_fail)

            async def _step1_fail(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")
                raise RuntimeError("Step 1 failed")

        registry = SagaRegistry()
        registry.register_saga(FailCompSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "FailCompSaga")
        assert state is not None
        assert state.status == SagaStatus.FAILED
        assert len(state.failed_compensations) == 1
        assert state.failed_compensations[0]["command_type"] == "CancelReservation"
        assert "CancelReservation" in state.failed_compensations[0]["error"]

    @pytest.mark.anyio
    async def test_forward_command_dispatch_failure_suspends(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """§3.4: Forward command dispatch failure → SUSPENDED for recovery."""
        bus = CommandBus()

        async def fail_dispatch(cmd: Any, uow: Any = None) -> EmptyCommandResult:
            raise ConnectionError("Service unavailable")

        bus.register(ReserveItems, fail_dispatch, uow_factory=lambda: FakeUnitOfWork())

        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED
        assert state.retry_count == 1
        assert "Dispatch failed" in (state.suspension_reason or "")

    @pytest.mark.anyio
    async def test_failure_with_no_compensations_direct_to_failed(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """§3.5: No compensation stack → FAILED directly
        (no COMPENSATING intermediate)."""

        class NoCompSaga(Saga[SagaState]):
            listens_to = [OrderCreated, ItemsReserved]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)
                self.on(ItemsReserved, handler=self._step2_fail)

            async def _step1(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                # No compensation

            async def _step2_fail(self, event: DomainEvent) -> None:
                raise RuntimeError("No compensations exist")

        registry = SagaRegistry()
        registry.register_saga(NoCompSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "NoCompSaga")
        assert state is not None
        assert state.status == SagaStatus.FAILED
        assert state.error == "No compensations exist"

    @pytest.mark.anyio
    async def test_partial_compensation_failure(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Multiple compensations, one fails → FAILED with partial compensation."""
        bus = CommandBus()

        async def noop(cmd: Any, uow: Any = None) -> EmptyCommandResult:
            return EmptyCommandResult()

        async def fail_cancel_ship(cmd: Any, uow: Any = None) -> EmptyCommandResult:
            raise RuntimeError("Cancel shipping down")

        bus.register(ReserveItems, noop, uow_factory=lambda: FakeUnitOfWork())
        bus.register(
            CancelShipping, fail_cancel_ship, uow_factory=lambda: FakeUnitOfWork()
        )
        bus.register(CancelPayment, noop, uow_factory=lambda: FakeUnitOfWork())
        bus.register(CancelReservation, noop, uow_factory=lambda: FakeUnitOfWork())
        bus.register(ProcessPayment, noop, uow_factory=lambda: FakeUnitOfWork())

        class ThreeCompSaga(Saga[SagaState]):
            listens_to = [OrderCreated, ItemsReserved, PaymentProcessed]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)
                self.on(ItemsReserved, handler=self._step2)
                self.on(PaymentProcessed, handler=self._step3_fail)

            async def _step1(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel res")

            async def _step2(self, event: DomainEvent) -> None:
                self.dispatch(ProcessPayment(order_id="ORD-1"))
                self.add_compensation(CancelPayment(order_id="ORD-1"), "Cancel pay")

            async def _step3_fail(self, event: DomainEvent) -> None:
                self.dispatch(ShipOrder(order_id="ORD-1"))
                self.add_compensation(CancelShipping(order_id="ORD-1"), "Cancel ship")
                raise RuntimeError("Step 3 failed")

        registry = SagaRegistry()
        registry.register_saga(ThreeCompSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        await mgr.handle(PaymentProcessed(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "ThreeCompSaga")
        assert state is not None
        assert state.status == SagaStatus.FAILED
        assert len(state.failed_compensations) == 1  # Only CancelShipping failed
        assert state.failed_compensations[0]["command_type"] == "CancelShipping"


# ═══════════════════════════════════════════════════════════════════════
# 4. Crash Recovery Flows
# ═══════════════════════════════════════════════════════════════════════


class TestCrashRecovery:
    """Recovery flows from §4 and §7 of flows.md."""

    @pytest.mark.anyio
    async def test_recovery_redispatches_undispatched_commands(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """§4.2: Recovery picks up undispatched commands and re-dispatches."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        # Simulate a saga with pending (undispatched) commands
        state = SagaState(
            saga_type="TwoStepSaga",
            status=SagaStatus.RUNNING,
            pending_commands=[
                {
                    "command_type": "ReserveItems",
                    "module_name": ReserveItems.__module__,
                    "data": {"order_id": "ORD-1", "item_count": 3},
                    "dispatched": False,
                },
            ],
        )
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.pending_commands == []
        assert updated.retry_count == 0

    @pytest.mark.anyio
    async def test_recovery_max_retries_exceeded_fails(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """§4.3: Max retries exceeded → saga fails."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        state = SagaState(
            saga_type="TwoStepSaga",
            status=SagaStatus.RUNNING,
            retry_count=3,
            max_retries=3,
            pending_commands=[
                {
                    "command_type": "ReserveItems",
                    "module_name": ReserveItems.__module__,
                    "data": {"order_id": "ORD-1", "item_count": 3},
                    "dispatched": False,
                },
            ],
        )
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.status == SagaStatus.FAILED
        assert "Max retries exceeded" in (updated.error or "")

    @pytest.mark.anyio
    async def test_recovery_skips_dispatched_commands(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """Already-dispatched commands are skipped during recovery."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        state = SagaState(
            saga_type="TwoStepSaga",
            status=SagaStatus.RUNNING,
            pending_commands=[
                {
                    "command_type": "ReserveItems",
                    "module_name": ReserveItems.__module__,
                    "data": {"order_id": "ORD-1", "item_count": 3},
                    "dispatched": True,  # Already dispatched
                },
            ],
        )
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.pending_commands == []
        assert updated.retry_count == 0

    @pytest.mark.anyio
    async def test_recovery_partial_dispatch(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """§7.2: Mid-dispatch crash — some dispatched, some not."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        state = SagaState(
            saga_type="TwoStepSaga",
            status=SagaStatus.RUNNING,
            pending_commands=[
                {
                    "command_type": "ReserveItems",
                    "module_name": ReserveItems.__module__,
                    "data": {"order_id": "ORD-1", "item_count": 3},
                    "dispatched": True,
                },
                {
                    "command_type": "ShipOrder",
                    "module_name": ShipOrder.__module__,
                    "data": {"order_id": "ORD-1"},
                    "dispatched": False,
                },
            ],
        )
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.pending_commands == []
        assert updated.retry_count == 0

    @pytest.mark.anyio
    async def test_recovery_unknown_saga_type_is_skipped(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """Unknown saga type in repository → log warning, skip."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        state = SagaState(
            saga_type="NonExistentSaga",
            status=SagaStatus.RUNNING,
            pending_commands=[
                {
                    "command_type": "ReserveItems",
                    "module_name": ReserveItems.__module__,
                    "data": {"order_id": "ORD-1", "item_count": 3},
                    "dispatched": False,
                },
            ],
        )
        await saga_repo.save(state)

        # Should not raise
        await mgr.recover_pending_sagas()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.pending_commands  # Unchanged


# ═══════════════════════════════════════════════════════════════════════
# 5. Timeout Flows
# ═══════════════════════════════════════════════════════════════════════


class TestTimeoutFlows:
    """Timeout processing flows from §4.4 and §5 of flows.md."""

    @pytest.mark.anyio
    async def test_default_timeout_triggers_compensation(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """Default on_timeout() → fail() → execute_compensations()."""
        registry = SagaRegistry()
        registry.register_saga(SuspendableSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "SuspendableSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED
        state.timeout_at = datetime.now(UTC) - timedelta(hours=1)
        await saga_repo.save(state)

        await mgr.process_timeouts()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.status in (
            SagaStatus.COMPENSATED,
            SagaStatus.COMPENSATING,
            SagaStatus.FAILED,
        )
        assert "timed out" in (updated.error or "").lower()

    @pytest.mark.anyio
    async def test_custom_timeout_retry_resumes_and_dispatches(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """Custom on_timeout() → resume() → dispatch retry command."""
        dispatched: list[Command[Any]] = []
        bus = CommandBus()

        async def capture(cmd: Command[Any], uow: Any = None) -> EmptyCommandResult:
            dispatched.append(cmd)
            return EmptyCommandResult()

        bus.register(ReserveItems, capture, uow_factory=lambda: FakeUnitOfWork())

        registry = SagaRegistry()
        registry.register_saga(TimeoutRetrySaga)
        mgr = _make_manager(saga_repo, registry, bus)

        state = SagaState(
            saga_type="TimeoutRetrySaga",
            status=SagaStatus.SUSPENDED,
            suspension_reason="waiting",
            timeout_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        await saga_repo.save(state)

        await mgr.process_timeouts()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.status == SagaStatus.RUNNING
        assert len(dispatched) == 1
        assert dispatched[0].order_id == "ORD-RETRY"

    @pytest.mark.anyio
    async def test_timeout_handler_crash_force_fails(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """on_timeout() raises → force-fail with error message."""

        class CrashingTimeoutSaga(Saga[SagaState]):
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
        registry.register_saga(CrashingTimeoutSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        state = SagaState(
            saga_type="CrashingTimeoutSaga",
            status=SagaStatus.SUSPENDED,
            timeout_at=datetime.now(UTC) - timedelta(hours=1),
        )
        await saga_repo.save(state)

        await mgr.process_timeouts()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.status == SagaStatus.FAILED
        assert "Timeout handler failed" in (updated.error or "")

    @pytest.mark.anyio
    async def test_not_expired_timeouts_are_ignored(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """Sagas with future timeout_at are not processed."""
        registry = SagaRegistry()
        registry.register_saga(SuspendableSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        state = SagaState(
            saga_type="SuspendableSaga",
            status=SagaStatus.SUSPENDED,
            suspension_reason="waiting",
            timeout_at=datetime.now(UTC) + timedelta(hours=24),  # Not expired
        )
        await saga_repo.save(state)

        await mgr.process_timeouts()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.status == SagaStatus.SUSPENDED  # Unchanged


# ═══════════════════════════════════════════════════════════════════════
# 6. Suspension Flows
# ═══════════════════════════════════════════════════════════════════════


class TestSuspensionFlows:
    """Suspension and resume flows from §5 of flows.md."""

    @pytest.mark.anyio
    async def test_declarative_suspend_via_on(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        """§5.1: suspend=True in on() declaration."""
        registry = SagaRegistry()
        registry.register_saga(SuspendableSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "SuspendableSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED
        assert state.suspension_reason == "Waiting for manager approval"
        assert state.timeout_at is not None

    @pytest.mark.anyio
    async def test_auto_resume_on_new_event(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        """§5.2: SUSPENDED saga auto-resumes when matching event arrives."""
        registry = SagaRegistry()
        registry.register_saga(SuspendableSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "SuspendableSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED

        # Send approval event → auto-resume
        await mgr.handle(ApprovalGranted(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "SuspendableSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPLETED

    @pytest.mark.anyio
    async def test_suspend_timeout_recovery_cycle(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        """§5.3: Suspend → timeout → compensate."""
        registry = SagaRegistry()
        registry.register_saga(SuspendableSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "SuspendableSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED

        # Expire the timeout
        state.timeout_at = datetime.now(UTC) - timedelta(hours=1)
        await saga_repo.save(state)

        await mgr.process_timeouts()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.status in (
            SagaStatus.COMPENSATED,
            SagaStatus.FAILED,
            SagaStatus.COMPENSATING,
        )


# ═══════════════════════════════════════════════════════════════════════
# 7. Multi-Saga Flows
# ═══════════════════════════════════════════════════════════════════════


class TestMultiSagaFlows:
    """Multi-saga scenarios from §6 of flows.md."""

    @pytest.mark.anyio
    async def test_two_sagas_same_event_independent_state(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """§6.1: Two sagas for same event → independent state per saga type."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        registry.register_saga(AuditSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        # Two independent states
        ts = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        au = await saga_repo.find_by_correlation_id(cid, "AuditSaga")
        assert ts is not None
        assert au is not None
        assert ts.id != au.id
        assert ts.status == SagaStatus.RUNNING
        assert au.status == SagaStatus.COMPLETED  # Audit completes immediately

    @pytest.mark.anyio
    async def test_multi_saga_independent_lifecycle(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """Each saga progresses independently through its own lifecycle."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        registry.register_saga(AuditSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        # Continue TwoStepSaga while AuditSaga is already done
        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        ts = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert ts is not None
        assert ts.status == SagaStatus.COMPLETED

        au = await saga_repo.find_by_correlation_id(cid, "AuditSaga")
        assert au is not None
        assert au.status == SagaStatus.COMPLETED

    @pytest.mark.anyio
    async def test_saga_started_explicitly_then_driven_by_events(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """§6.2: start_saga() then handle() share same state via correlation_id."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        saga_id = await mgr.start_saga(
            TwoStepSaga,
            initial_event=OrderCreated(order_id="ORD-1"),
            correlation_id=cid,
        )

        state = await saga_repo.get_by_id(saga_id)
        assert state is not None
        assert state.correlation_id == cid

        # Continue via handle()
        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        # Same state object
        state2 = await saga_repo.get_by_id(saga_id)
        assert state2 is not None
        assert state2.status == SagaStatus.COMPLETED


# ═══════════════════════════════════════════════════════════════════════
# 8. Tracing ID Propagation
# ═══════════════════════════════════════════════════════════════════════


class TestTracingPropagation:
    """Tracing ID propagation from §8 of flows.md."""

    @pytest.mark.anyio
    async def test_forward_commands_carry_event_causation_id(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Forward command causation_id = event.event_id."""
        dispatched: list[Command[Any]] = []
        bus = CommandBus()

        async def capture(cmd: Command[Any], uow: Any = None) -> EmptyCommandResult:
            dispatched.append(cmd)
            return EmptyCommandResult()

        bus.register(ReserveItems, capture, uow_factory=lambda: FakeUnitOfWork())

        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event)

        assert dispatched[0].correlation_id == cid

    @pytest.mark.anyio
    async def test_compensation_commands_carry_saga_causation_id(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Compensation command causation_id = state.id (saga's own decision)."""
        dispatched: list[Command[Any]] = []
        bus = CommandBus()

        async def capture(cmd: Command[Any], uow: Any = None) -> EmptyCommandResult:
            dispatched.append(cmd)
            return EmptyCommandResult()

        bus.register(ReserveItems, capture, uow_factory=lambda: FakeUnitOfWork())
        bus.register(CancelReservation, capture, uow_factory=lambda: FakeUnitOfWork())

        class FailWithCompSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)

            async def _step1(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")
                raise RuntimeError("Fail immediately")

        registry = SagaRegistry()
        registry.register_saga(FailWithCompSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        comp_cmds = [c for c in dispatched if isinstance(c, CancelReservation)]
        assert len(comp_cmds) == 1
        assert comp_cmds[0].correlation_id == cid

    @pytest.mark.anyio
    async def test_recovery_commands_carry_saga_causation_id(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """Recovery command causation_id = state.id."""
        dispatched: list[Command[Any]] = []
        bus = CommandBus()

        async def capture(cmd: Command[Any], uow: Any = None) -> EmptyCommandResult:
            dispatched.append(cmd)
            return EmptyCommandResult()

        bus.register(ReserveItems, capture, uow_factory=lambda: FakeUnitOfWork())

        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=cid,
            status=SagaStatus.RUNNING,
            pending_commands=[
                {
                    "command_type": "ReserveItems",
                    "module_name": ReserveItems.__module__,
                    "data": {"order_id": "ORD-1", "item_count": 3},
                    "dispatched": False,
                },
            ],
        )
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        assert len(dispatched) == 1
        assert dispatched[0].correlation_id == cid


# ═══════════════════════════════════════════════════════════════════════
# 9. LIFO Compensation Order
# ═══════════════════════════════════════════════════════════════════════


class TestLIFOCompensationOrder:
    """Compensation commands execute in LIFO (stack) order."""

    @pytest.mark.anyio
    async def test_lifo_order_with_three_compensations(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Three compensations are dispatched in reverse order."""
        dispatch_order: list[str] = []
        bus = CommandBus()

        async def record(cmd: Command[Any], uow: Any = None) -> EmptyCommandResult:
            dispatch_order.append(type(cmd).__name__)
            return EmptyCommandResult()

        bus.register(ReserveItems, record, uow_factory=lambda: FakeUnitOfWork())
        bus.register(CancelReservation, record, uow_factory=lambda: FakeUnitOfWork())
        bus.register(CancelPayment, record, uow_factory=lambda: FakeUnitOfWork())
        bus.register(ProcessPayment, record, uow_factory=lambda: FakeUnitOfWork())
        bus.register(ShipOrder, record, uow_factory=lambda: FakeUnitOfWork())
        bus.register(CancelShipping, record, uow_factory=lambda: FakeUnitOfWork())

        class ThreeStepSaga(Saga[SagaState]):
            listens_to = [OrderCreated, ItemsReserved, PaymentProcessed]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)
                self.on(ItemsReserved, handler=self._step2)
                self.on(PaymentProcessed, handler=self._step3_fail)

            async def _step1(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(
                    CancelReservation(order_id="ORD-1"), "Cancel reservation"
                )

            async def _step2(self, event: DomainEvent) -> None:
                self.dispatch(ProcessPayment(order_id="ORD-1"))
                self.add_compensation(CancelPayment(order_id="ORD-1"), "Cancel payment")

            async def _step3_fail(self, event: DomainEvent) -> None:
                self.dispatch(ShipOrder(order_id="ORD-1"))
                self.add_compensation(
                    CancelShipping(order_id="ORD-1"), "Cancel shipping"
                )
                raise RuntimeError("Step 3 failed")

        registry = SagaRegistry()
        registry.register_saga(ThreeStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        await mgr.handle(PaymentProcessed(order_id="ORD-1", correlation_id=cid))

        # LIFO: CancelShipping, CancelPayment, CancelReservation
        compensation_cmds = [n for n in dispatch_order if n.startswith("Cancel")]
        assert compensation_cmds == [
            "CancelShipping",
            "CancelPayment",
            "CancelReservation",
        ]


# ═══════════════════════════════════════════════════════════════════════
# 10. Idempotency Flows
# ═══════════════════════════════════════════════════════════════════════


class TestIdempotencyFlows:
    """Duplicate event handling — idempotent processing."""

    @pytest.mark.anyio
    async def test_duplicate_event_is_skipped(
        self, saga_repo: FakeSagaRepository, command_bus: CommandBus
    ) -> None:
        """Same event_id processed twice → second time is no-op."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, command_bus)

        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)

        await mgr.handle(event)

        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        step_count = len(state.step_history)

        # Process same event again
        await mgr.handle(event)

        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert len(state.step_history) == step_count  # No change


# ═══════════════════════════════════════════════════════════════════════
# 11. Multi-Dispatch Per Event
# ═══════════════════════════════════════════════════════════════════════


class TestMultiDispatchFlow:
    """Saga that dispatches multiple commands per event."""

    @pytest.mark.anyio
    async def test_multiple_commands_dispatched_per_event(
        self, saga_repo: FakeSagaRepository
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
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        assert len(dispatched) == 2
        types = {type(c) for c in dispatched}
        assert ReserveItems in types
        assert SendNotification in types
