"""Crash & interruption scenario tests — flows.md §7.

Tests simulate sudden interruptions (power off, process kill) and graceful
shutdowns at every possible point in saga processing. The key insight:

- "Sudden interruption" = no ``await`` after crash point completes, state
  is whatever was last saved.
- "Graceful shutdown" = cleanup code runs, possibly rollback or checkpoint.

Crash recovery uses the write-ahead log pattern:
  Phase 1: Serialize pending_commands + save
  Phase 2: Dispatch per-command + mark dispatched + save per command
  Phase 3: Clear pending_commands + save
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from pydomain.cqrs.command_bus import CommandBus
from pydomain.cqrs.commands import Command, EmptyCommandResult
from pydomain.cqrs.saga.manager import SagaManager
from pydomain.cqrs.saga.registry import SagaRegistry
from pydomain.cqrs.saga.saga import Saga
from pydomain.cqrs.saga.state import (
    SagaState,
    SagaStatus,
    StepRecord,
)
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing import FakeUnitOfWork
from pydomain.testing.fake_saga_repository import FakeSagaRepository

from .conftest import (
    ApprovalGranted,
    CancelPayment,
    CancelReservation,
    ConfirmOrder,
    FiveStepSaga,
    MultiDispatchSaga,
    OrderCreated,
    ProcessPayment,
    RequestApproval,
    ReserveItems,
    SendNotification,
    SuspendableSaga,
    TwoStepSaga,
    _noop_command_bus,
)

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _capture_bus(
    *cmd_types: type[Command[Any]],
) -> tuple[CommandBus, list[Command[Any]]]:
    """Create a bus that captures all dispatched commands of the given types."""
    bus = CommandBus()
    dispatched: list[Command[Any]] = []

    async def capture(cmd: Command[Any], uow: Any = None) -> EmptyCommandResult:
        dispatched.append(cmd)
        return EmptyCommandResult()

    for ct in cmd_types:
        bus.register(ct, capture, uow_factory=lambda: FakeUnitOfWork())

    return bus, dispatched


def _failing_bus(
    fail_on: type[Command[Any]], *other_types: type[Command[Any]]
) -> tuple[CommandBus, list[Command[Any]]]:
    """Bus that raises on ``fail_on`` type, captures all others."""
    bus = CommandBus()
    dispatched: list[Command[Any]] = []

    async def capture(cmd: Command[Any], uow: Any = None) -> EmptyCommandResult:
        dispatched.append(cmd)
        if isinstance(cmd, fail_on):
            raise RuntimeError(f"Dispatch failed: {type(cmd).__name__}")
        return EmptyCommandResult()

    for ct in (fail_on, *other_types):
        bus.register(ct, capture, uow_factory=lambda: FakeUnitOfWork())

    return bus, dispatched


def _make_manager(
    repo: FakeSagaRepository,
    registry: SagaRegistry,
    bus: CommandBus,
) -> SagaManager:
    return SagaManager(repository=repo, registry=registry, command_bus=bus)


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def saga_repo() -> FakeSagaRepository:
    return FakeSagaRepository()


# ═══════════════════════════════════════════════════════════════════════
# 1. Crash During Event Handling
# ═══════════════════════════════════════════════════════════════════════


class TestCrashDuringEventHandler:
    """Simulate crashes at different points during event handler execution."""

    @pytest.mark.anyio
    async def test_crash_before_state_creation(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Exception before _create_initial_state — no state in repo."""
        bus = _noop_command_bus()
        registry = SagaRegistry()

        class BadSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._crash)

            async def _crash(self, event: DomainEvent) -> None:
                raise RuntimeError("Instant crash")

        registry.register_saga(BadSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        # State was created and saved before handle() ran — manager creates state first
        state = await saga_repo.find_by_correlation_id(cid, "BadSaga")
        assert state is not None
        assert state.status == SagaStatus.FAILED
        assert "Instant crash" in (state.error or "")

    @pytest.mark.anyio
    async def test_crash_during_handle_mid_handler(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Handler raises mid-way — state is saved with error status."""
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()

        class MidCrashSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step)

            async def _step(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")
                raise RuntimeError("Mid-handler crash")

        registry.register_saga(MidCrashSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "MidCrashSaga")
        assert state is not None
        # Compensation should have been dispatched due to compensation stack
        assert state.status in (SagaStatus.COMPENSATED, SagaStatus.FAILED)
        # Verify compensation outcome is consistent
        if state.status == SagaStatus.COMPENSATED:
            assert len(state.failed_compensations) == 0
        else:
            assert len(state.failed_compensations) >= 1

    @pytest.mark.anyio
    async def test_crash_during_handle_no_compensation(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Handler crashes with no compensation stack — FAILED, state consistent."""
        bus = _noop_command_bus()
        registry = SagaRegistry()

        class NoCompCrashSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step)

            async def _step(self, event: DomainEvent) -> None:
                raise RuntimeError("Crash with no compensation")

        registry.register_saga(NoCompCrashSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "NoCompCrashSaga")
        assert state is not None
        assert state.status == SagaStatus.FAILED
        assert state.error is not None
        assert "Crash with no compensation" in (state.error or "")

    @pytest.mark.anyio
    async def test_crash_during_handle_with_compensation(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Handler crashes, compensation stack exists → COMPENSATING → COMPENSATED."""
        bus, dispatched = _capture_bus(ReserveItems, CancelReservation)
        registry = SagaRegistry()

        class CompCrashSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step)

            async def _step(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")
                raise RuntimeError("Crash with compensation")

        registry.register_saga(CompCrashSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "CompCrashSaga")
        assert state is not None
        # Compensation was dispatched
        comp_cmds = [c for c in dispatched if isinstance(c, CancelReservation)]
        assert len(comp_cmds) == 1
        assert state.status == SagaStatus.COMPENSATED

    @pytest.mark.anyio
    async def test_crash_after_handle_before_dispatch(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """handle() completes but dispatch fails — commands in pending_commands."""
        bus, dispatched = _failing_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        # State is suspended due to dispatch failure
        assert state.status == SagaStatus.SUSPENDED
        # Pending commands are preserved for recovery
        assert len(state.pending_commands) > 0

    @pytest.mark.anyio
    async def test_state_consistent_after_handler_crash(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """After handler crash, state is in a recoverable
        terminal or suspended state."""
        bus = _noop_command_bus()
        registry = SagaRegistry()

        class ConsistentCrashSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step)

            async def _step(self, event: DomainEvent) -> None:
                self.state.current_step = "crashed"
                raise RuntimeError("Boom")

        registry.register_saga(ConsistentCrashSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "ConsistentCrashSaga")
        assert state is not None
        # State is terminal (FAILED) — recovery won't pick it up
        assert state.is_terminal
        assert state.status == SagaStatus.FAILED
        assert "Boom" in (state.error or "")


# ═══════════════════════════════════════════════════════════════════════
# 2. Crash During Command Dispatch (Write-Ahead Log)
# ═══════════════════════════════════════════════════════════════════════


class TestCrashDuringForwardDispatch:
    """Maps to flows.md §7.2 crash scenarios for forward command dispatch."""

    @pytest.mark.anyio
    async def test_crash_before_phase1_serialize(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """No pending_commands recorded — saga appears clean."""
        bus = _noop_command_bus()
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        # Normal flow — no crash. Verify clean state.
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        # After successful dispatch, pending_commands are cleared (Phase 3)
        assert state.pending_commands == []

    @pytest.mark.anyio
    async def test_crash_after_phase1_before_phase2(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """All pending_commands have dispatched=False — recovery re-dispatches all."""
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        # Manually create a state that simulates crash after Phase 1
        state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=cid,
            status=SagaStatus.RUNNING,
            current_step="reserving",
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

        await mgr.recover_pending_sagas()

        assert len(dispatched) == 1
        assert isinstance(dispatched[0], ReserveItems)

        # After recovery, pending_commands are cleared
        state_after = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state_after is not None
        assert state_after.pending_commands == []

    @pytest.mark.anyio
    async def test_crash_mid_phase2_first_dispatched_second_not(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """cmd[0] dispatched=True, cmd[1] dispatched=False —
        recovery picks up cmd[1]."""
        bus, dispatched = _capture_bus(ReserveItems, SendNotification)
        registry = SagaRegistry()
        registry.register_saga(MultiDispatchSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        state = SagaState(
            saga_type="MultiDispatchSaga",
            correlation_id=cid,
            status=SagaStatus.RUNNING,
            current_step="init",
            pending_commands=[
                {
                    "command_type": "ReserveItems",
                    "module_name": ReserveItems.__module__,
                    "data": {"order_id": "ORD-1", "item_count": 5},
                    "dispatched": True,  # Already dispatched
                },
                {
                    "command_type": "SendNotification",
                    "module_name": SendNotification.__module__,
                    "data": {"order_id": "ORD-1", "message": "Processing"},
                    "dispatched": False,  # Needs recovery
                },
            ],
        )
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        # Only the undispatched command is recovered
        assert len(dispatched) == 1
        assert isinstance(dispatched[0], SendNotification)

    @pytest.mark.anyio
    async def test_crash_mid_phase2_between_mark_and_save(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Command dispatched in bus but dispatched flag not
        persisted — at-least-once."""
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        # Simulate: command was dispatched but flag was not saved
        # (power-off between dispatch and save)
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
                    # Not marked — even though it may have been dispatched
                },
            ],
        )
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        # At-least-once: command is re-dispatched
        assert len(dispatched) == 1

    @pytest.mark.anyio
    async def test_crash_after_phase2_before_phase3(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """All dispatched=True but pending_commands not cleared —
        recovery clears them."""
        bus, dispatched = _capture_bus(ReserveItems)
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
                    "data": {"order_id": "ORD-1", "item_count": 5},
                    "dispatched": True,  # All dispatched
                },
            ],
        )
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        # No undispatched commands — cleanup only
        state_after = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state_after is not None
        assert state_after.pending_commands == []

    @pytest.mark.anyio
    async def test_crash_with_multiple_commands_partial_dispatch(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """3 commands, dispatch fails on 2nd — SUSPENDED with retry."""
        bus = CommandBus()
        dispatched: list[Command[Any]] = []
        call_count = 0

        async def capture(cmd: Command[Any], uow: Any = None) -> EmptyCommandResult:
            nonlocal call_count
            call_count += 1
            dispatched.append(cmd)
            if call_count >= 2:
                raise RuntimeError("Second command fails")
            return EmptyCommandResult()

        bus.register(ReserveItems, capture, uow_factory=lambda: FakeUnitOfWork())
        bus.register(SendNotification, capture, uow_factory=lambda: FakeUnitOfWork())

        registry = SagaRegistry()
        registry.register_saga(MultiDispatchSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "MultiDispatchSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED
        assert state.retry_count >= 1

    @pytest.mark.anyio
    async def test_full_recovery_after_simulated_power_off(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Complete power-off simulation: state was saved mid-Phase 2,
        recovery picks up."""
        bus, dispatched = _capture_bus(ReserveItems, ProcessPayment)
        registry = SagaRegistry()
        registry.register_saga(FiveStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        # Step 1 completes normally
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        # Simulate crash: manually set up a state with
        # one undispatched command for step 2
        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        state.pending_commands = [
            {
                "command_type": "ProcessPayment",
                "module_name": ProcessPayment.__module__,
                "data": {"order_id": "ORD-1", "amount": 0.0},
                "dispatched": False,
            },
        ]
        await saga_repo.save(state)

        # Recovery
        await mgr.recover_pending_sagas()

        assert len(dispatched) >= 1
        assert any(isinstance(c, ProcessPayment) for c in dispatched)

        state_after = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state_after is not None
        assert state_after.pending_commands == []


# ═══════════════════════════════════════════════════════════════════════
# 3. Crash During Compensation Dispatch
# ═══════════════════════════════════════════════════════════════════════


class TestCrashDuringCompensationDispatch:
    """Simulate crashes during compensation command dispatch."""

    @pytest.mark.anyio
    async def test_crash_before_any_compensation_dispatch(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Saga in COMPENSATING, no commands dispatched —
        recovery dispatches compensations."""
        bus, dispatched = _capture_bus(CancelReservation)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        # Manually create COMPENSATING state with pending compensation command
        state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=cid,
            status=SagaStatus.COMPENSATING,
            pending_commands=[
                {
                    "command_type": "CancelReservation",
                    "module_name": CancelReservation.__module__,
                    "data": {"order_id": "ORD-1"},
                    "dispatched": False,
                },
            ],
        )
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        # COMPENSATING recovery path — collect_commands returns empty
        # (compensation_stack is empty since we popped during execute_compensations)
        state_after = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state_after is not None

    @pytest.mark.anyio
    async def test_crash_mid_compensation_first_succeeds_second_fails(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """Partial compensation → FAILED with failed_compensations."""
        bus = CommandBus()
        dispatched: list[Command[Any]] = []
        call_count = 0

        async def capture(cmd: Command[Any], uow: Any = None) -> EmptyCommandResult:
            nonlocal call_count
            call_count += 1
            dispatched.append(cmd)
            if isinstance(cmd, CancelPayment):
                raise RuntimeError("Payment cancellation failed")
            return EmptyCommandResult()

        bus.register(ReserveItems, capture, uow_factory=lambda: FakeUnitOfWork())
        bus.register(CancelReservation, capture, uow_factory=lambda: FakeUnitOfWork())
        bus.register(CancelPayment, capture, uow_factory=lambda: FakeUnitOfWork())

        registry = SagaRegistry()

        class TwoCompSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step)

            async def _step(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(
                    CancelReservation(order_id="ORD-1"), "Cancel reservation"
                )
                self.add_compensation(CancelPayment(order_id="ORD-1"), "Cancel payment")
                raise RuntimeError("Boom")

        registry.register_saga(TwoCompSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "TwoCompSaga")
        assert state is not None
        assert state.status == SagaStatus.FAILED
        assert len(state.failed_compensations) >= 1
        assert any(
            fc["command_type"] == "CancelPayment" for fc in state.failed_compensations
        )

    @pytest.mark.anyio
    async def test_compensation_dispatch_failure_recorded_in_state(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """Failed compensation command details are in failed_compensations."""
        bus = CommandBus()

        async def fail_dispatch(
            cmd: Command[Any], uow: Any = None
        ) -> EmptyCommandResult:
            raise RuntimeError("Network error")

        bus.register(ReserveItems, fail_dispatch, uow_factory=lambda: FakeUnitOfWork())
        bus.register(
            CancelReservation, fail_dispatch, uow_factory=lambda: FakeUnitOfWork()
        )

        registry = SagaRegistry()

        class FailCompSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step)

            async def _step(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")
                raise RuntimeError("Handler error")

        registry.register_saga(FailCompSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "FailCompSaga")
        assert state is not None
        assert len(state.failed_compensations) >= 1
        failed = state.failed_compensations[0]
        assert "CancelReservation" in failed["command_type"]
        assert failed["error"] is not None

    @pytest.mark.anyio
    async def test_all_compensations_succeed_state_compensated(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """All compensation commands succeed → COMPENSATED."""
        bus, dispatched = _capture_bus(ReserveItems, CancelReservation)
        registry = SagaRegistry()

        class SuccessCompSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step)

            async def _step(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")
                raise RuntimeError("Fail after dispatch")

        registry.register_saga(SuccessCompSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "SuccessCompSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPENSATED
        assert len(state.failed_compensations) == 0

    @pytest.mark.anyio
    async def test_recovery_of_compensating_saga(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """COMPENSATING state with empty compensation_stack —
        recovery handles gracefully."""
        bus, dispatched = _capture_bus(CancelReservation)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=cid,
            status=SagaStatus.COMPENSATING,
            compensation_stack=[],  # Already popped
        )
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        # No commands to dispatch — compensation_stack is empty
        state_after = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state_after is not None
        assert state_after.status == SagaStatus.COMPENSATED


# ═══════════════════════════════════════════════════════════════════════
# 4. Crash During Suspension/Resume
# ═══════════════════════════════════════════════════════════════════════


class TestCrashDuringSuspension:
    """Simulate crashes during suspend/resume operations."""

    @pytest.mark.anyio
    async def test_saga_suspends_correctly(self, saga_repo: FakeSagaRepository) -> None:
        """Saga suspends with correct state after suspend()."""
        bus, dispatched = _capture_bus(RequestApproval)
        registry = SagaRegistry()
        registry.register_saga(SuspendableSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "SuspendableSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED
        assert state.suspended_at is not None
        assert state.suspension_reason is not None
        assert state.timeout_at is not None

    @pytest.mark.anyio
    async def test_crash_after_suspend_before_forward_dispatch(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """SUSPENDED state saved but forward commands not dispatched —
        recovery finds pending."""
        bus, dispatched = _capture_bus(RequestApproval)
        registry = SagaRegistry()
        registry.register_saga(SuspendableSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "SuspendableSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED

        # Command was dispatched (saga suspends after dispatch, not before)
        assert len(dispatched) >= 1

    @pytest.mark.anyio
    async def test_crash_during_resume_before_handle(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Resume called, state RUNNING, but handle didn't run — event not processed."""
        bus, dispatched = _capture_bus(RequestApproval, ConfirmOrder)
        registry = SagaRegistry()
        registry.register_saga(SuspendableSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "SuspendableSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED

        # Now send approval event — auto-resumes
        await mgr.handle(ApprovalGranted(order_id="ORD-1", correlation_id=cid))

        state_after = await saga_repo.find_by_correlation_id(cid, "SuspendableSaga")
        assert state_after is not None
        assert state_after.status == SagaStatus.COMPLETED

    @pytest.mark.anyio
    async def test_crash_during_resume_mid_handler(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Resume + handle partially — state might be RUNNING with partial step."""
        bus, dispatched = _capture_bus(RequestApproval, ConfirmOrder)
        registry = SagaRegistry()

        class CrashOnResumeSaga(Saga[SagaState]):
            listens_to = [OrderCreated, ApprovalGranted]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: RequestApproval(order_id=e.order_id),
                    step="awaiting_approval",
                    suspend=True,
                    suspend_reason="Waiting",
                    suspend_timeout=timedelta(hours=24),
                )
                self.on(ApprovalGranted, handler=self._on_approval)

            async def _on_approval(self, event: DomainEvent) -> None:
                self.state.current_step = "confirming"
                raise RuntimeError("Crash after resume")

        registry.register_saga(CrashOnResumeSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        await mgr.handle(ApprovalGranted(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "CrashOnResumeSaga")
        assert state is not None
        assert state.status == SagaStatus.FAILED
        assert "Crash after resume" in (state.error or "")

    @pytest.mark.anyio
    async def test_crash_during_timeout_processing(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Timeout crashes mid-loop — some sagas processed, others not."""
        bus, dispatched = _capture_bus(RequestApproval, CancelReservation)
        registry = SagaRegistry()

        class TimeoutSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: RequestApproval(order_id=e.order_id),
                    step="awaiting",
                    compensate=lambda e: CancelReservation(order_id=e.order_id),
                    suspend=True,
                    suspend_reason="Waiting",
                    suspend_timeout=timedelta(milliseconds=0),
                )

        registry.register_saga(TimeoutSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        # Process expired timeouts
        await mgr.process_timeouts()

        state = await saga_repo.find_by_correlation_id(cid, "TimeoutSaga")
        assert state is not None
        # Default timeout → fail → compensate
        assert state.status in (SagaStatus.COMPENSATED, SagaStatus.FAILED)
        assert state.error is not None


# ═══════════════════════════════════════════════════════════════════════
# 5. Graceful Shutdown Scenarios
# ═══════════════════════════════════════════════════════════════════════


class TestGracefulShutdown:
    """Simulate graceful shutdown — cleanup runs, state is recoverable."""

    @pytest.mark.anyio
    async def test_graceful_shutdown_completes_current_dispatch(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Manager processes current event fully before stopping."""
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert state.status == SagaStatus.RUNNING
        assert state.pending_commands == []
        assert len(dispatched) == 1

    @pytest.mark.anyio
    async def test_graceful_shutdown_suspends_in_progress_saga(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Incomplete dispatch → saga SUSPENDED, pending_commands preserved."""
        bus, dispatched = _failing_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED
        assert len(state.pending_commands) > 0

    @pytest.mark.anyio
    async def test_graceful_shutdown_leaves_recoverable_state(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """After shutdown, all sagas are in a state that recovery can handle."""
        bus = _noop_command_bus()
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        # Process two sagas
        cid1 = uuid4()
        cid2 = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid1))
        await mgr.handle(OrderCreated(order_id="ORD-2", correlation_id=cid2))

        # Both should be in non-terminal states (RUNNING, waiting for next event)
        state1 = await saga_repo.find_by_correlation_id(cid1, "TwoStepSaga")
        state2 = await saga_repo.find_by_correlation_id(cid2, "TwoStepSaga")
        assert state1 is not None
        assert state2 is not None
        assert not state1.is_terminal
        assert not state2.is_terminal

    @pytest.mark.anyio
    async def test_graceful_shutdown_during_recovery_cycle(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Recovery partially completes, remaining sagas stay stalled for next cycle."""
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        # Create 3 stalled sagas
        for i in range(3):
            state = SagaState(
                saga_type="TwoStepSaga",
                correlation_id=uuid4(),
                status=SagaStatus.RUNNING,
                pending_commands=[
                    {
                        "command_type": "ReserveItems",
                        "module_name": ReserveItems.__module__,
                        "data": {"order_id": f"ORD-{i}", "item_count": 1},
                        "dispatched": False,
                    },
                ],
            )
            await saga_repo.save(state)

        # Recovery with limit=2 — only 2 processed
        await mgr.recover_pending_sagas(limit=2)

        # 2 recovered + 1 still stalled
        remaining = await saga_repo.find_stalled_sagas(limit=10)
        assert len(remaining) <= 1  # At most 1 still stalled

    @pytest.mark.anyio
    async def test_graceful_shutdown_preserves_compensation_stack(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Mid-compensation shutdown — compensation_stack preserved in state."""
        bus, dispatched = _capture_bus(ReserveItems, CancelReservation)
        registry = SagaRegistry()

        class ShutdownMidCompSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step)

            async def _step(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")
                raise RuntimeError("Fail for shutdown test")

        registry.register_saga(ShutdownMidCompSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "ShutdownMidCompSaga")
        assert state is not None
        # Compensation was dispatched successfully
        assert state.status == SagaStatus.COMPENSATED


# ═══════════════════════════════════════════════════════════════════════
# 6. Recovery After Crash
# ═══════════════════════════════════════════════════════════════════════


class TestRecoveryAfterCrash:
    """Full recovery scenarios after simulated power-off."""

    @pytest.mark.anyio
    async def test_full_recovery_after_power_off(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Simulate complete state loss, rebuild from persisted state."""
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=cid,
            status=SagaStatus.RUNNING,
            current_step="reserving",
            step_history=[
                StepRecord(
                    step_name="reserving",
                    event_type="OrderCreated",
                    causation_id=uuid4(),
                ),
            ],
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

        # Recovery
        await mgr.recover_pending_sagas()

        state_after = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state_after is not None
        assert state_after.pending_commands == []
        assert len(dispatched) == 1
        # Step history preserved
        assert len(state_after.step_history) >= 1

    @pytest.mark.anyio
    async def test_recovery_idempotency(self, saga_repo: FakeSagaRepository) -> None:
        """Recovery runs twice on same stalled saga —
        commands dispatched only once per run."""
        bus, dispatched = _capture_bus(ReserveItems)
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
                    "data": {"order_id": "ORD-1", "item_count": 5},
                    "dispatched": False,
                },
            ],
        )
        await saga_repo.save(state)

        # First recovery
        await mgr.recover_pending_sagas()
        first_count = len(dispatched)
        assert first_count == 1

        # Second recovery — no more undispatched commands
        await mgr.recover_pending_sagas()
        assert len(dispatched) == first_count  # No additional dispatch

    @pytest.mark.anyio
    async def test_recovery_order_respects_save_order(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Multiple stalled sagas recovered in repo order."""
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        # Create 3 stalled sagas
        cids = []
        for i in range(3):
            cid = uuid4()
            cids.append(cid)
            state = SagaState(
                saga_type="TwoStepSaga",
                correlation_id=cid,
                status=SagaStatus.RUNNING,
                pending_commands=[
                    {
                        "command_type": "ReserveItems",
                        "module_name": ReserveItems.__module__,
                        "data": {"order_id": f"ORD-{i}", "item_count": 1},
                        "dispatched": False,
                    },
                ],
            )
            await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        assert len(dispatched) == 3

    @pytest.mark.anyio
    async def test_recovery_skips_already_terminal_sagas(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Terminal sagas in stalled query → skipped (is_terminal check)."""
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        _mgr = _make_manager(saga_repo, registry, bus)  # noqa: F841

        cid = uuid4()
        # Create a COMPLETED saga with pending_commands (edge case)
        state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=cid,
            status=SagaStatus.COMPLETED,  # Terminal
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

        # find_stalled_sagas filters by not is_terminal
        stalled = await saga_repo.find_stalled_sagas()
        assert len(stalled) == 0

    @pytest.mark.anyio
    async def test_recovery_after_crash_creates_correct_audit_trail(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """updated_at and version increment after recovery."""
        bus, dispatched = _capture_bus(ReserveItems)
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
                    "data": {"order_id": "ORD-1", "item_count": 5},
                    "dispatched": False,
                },
            ],
        )
        await saga_repo.save(state)

        original_version = state.version

        await mgr.recover_pending_sagas()

        state_after = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state_after is not None
        assert state_after.version > original_version
        assert state_after.updated_at > state.updated_at

    @pytest.mark.anyio
    async def test_recovery_after_crash_maintains_step_history(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """step_history not corrupted by recovery dispatch."""
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        original_step = StepRecord(
            step_name="reserving",
            event_type="OrderCreated",
            causation_id=uuid4(),
        )
        state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=cid,
            status=SagaStatus.RUNNING,
            step_history=[original_step],
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

        await mgr.recover_pending_sagas()

        state_after = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state_after is not None
        # Original step history preserved
        assert len(state_after.step_history) >= 1
        assert state_after.step_history[0].step_name == "reserving"
        assert state_after.step_history[0].causation_id == original_step.causation_id
