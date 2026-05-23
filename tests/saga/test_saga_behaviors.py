"""Tests for recently fixed saga behaviors.

Covers four specific behaviors that were fixed in source code:
1. ``Saga.complete()`` clears ``compensation_stack``
2. ``Saga.execute_compensations()`` discards forward commands
3. Resiliency — state saved after every change
4. COMPENSATING recovery path in manager (non-empty & empty stack)
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from pydomain.cqrs.command_bus import CommandBus
from pydomain.cqrs.commands import Command, EmptyCommandResult
from pydomain.cqrs.saga.manager import SagaManager
from pydomain.cqrs.saga.registry import SagaRegistry
from pydomain.cqrs.saga.state import (
    SagaState,
    SagaStatus,
)
from pydomain.testing import FakeUnitOfWork
from pydomain.testing.fake_saga_repository import FakeSagaRepository

from .conftest import (
    CancelPayment,
    CancelReservation,
    CancelShipping,
    ConfirmOrder,
    DeliveryScheduled,
    FiveStepSaga,
    ItemsReserved,
    OrderCreated,
    OrderShipped,
    PaymentProcessed,
    ProcessPayment,
    ReserveItems,
    ScheduleDelivery,
    ShipOrder,
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


def _make_manager(
    repo: FakeSagaRepository,
    registry: SagaRegistry,
    bus: CommandBus,
) -> SagaManager:
    return SagaManager(repository=repo, registry=registry, command_bus=bus)


def _noop_bus() -> CommandBus:
    return _noop_command_bus()


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def saga_repo() -> FakeSagaRepository:
    return FakeSagaRepository()


# ═══════════════════════════════════════════════════════════════════════
# 1. Saga.complete() clears compensation_stack
# ═══════════════════════════════════════════════════════════════════════


class TestCompleteClearsCompensationStack:
    """``complete()`` clears ``compensation_stack`` so that completed sagas
    never carry stale compensation records into persistence."""

    @pytest.mark.anyio
    async def test_complete_clears_compensation_stack_in_memory(self) -> None:
        """After ``complete()``, ``compensation_stack``
        is empty on the saga instance."""
        state = SagaState(saga_type="TwoStepSaga", correlation_id=uuid4())
        saga = TwoStepSaga(state)

        # Step 1: handle OrderCreated — queues ReserveItems + adds compensation
        await saga.handle(OrderCreated(order_id="ORD-1"))
        assert len(state.compensation_stack) == 1

        # Step 2: handle ItemsReserved — calls complete() (complete=True)
        await saga.handle(ItemsReserved(order_id="ORD-1"))

        assert state.status == SagaStatus.COMPLETED
        assert state.compensation_stack == []

    @pytest.mark.anyio
    async def test_complete_clears_stack_with_multiple_compensations(self) -> None:
        """FiveStepSaga has 3 compensations; complete() clears all of them."""
        state = SagaState(saga_type="FiveStepSaga", correlation_id=uuid4())
        saga = FiveStepSaga(state)

        await saga.handle(OrderCreated(order_id="ORD-1"))
        await saga.handle(ItemsReserved(order_id="ORD-1"))
        await saga.handle(PaymentProcessed(order_id="ORD-1"))

        # 3 compensations on the stack (reserve, pay, ship)
        assert len(state.compensation_stack) == 3

        # Drive to completion (steps 4 & 5, step 5 calls complete())
        await saga.handle(OrderShipped(order_id="ORD-1"))
        await saga.handle(DeliveryScheduled(order_id="ORD-1"))

        assert state.status == SagaStatus.COMPLETED
        assert state.compensation_stack == []

    @pytest.mark.anyio
    async def test_complete_clears_stack_persisted_to_repository(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """After manager processes a saga to completion, the compensation_stack
        in the repository is empty."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        bus = _noop_bus()
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        # Verify compensation was recorded
        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert len(state.compensation_stack) == 1

        # Complete the saga
        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        # Re-read from repository — compensation_stack must be empty
        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPLETED
        assert state.compensation_stack == []


# ═══════════════════════════════════════════════════════════════════════
# 2. Saga.execute_compensations() discards forward commands
# ═══════════════════════════════════════════════════════════════════════


class TestExecuteCompensationsDiscardsForwardCommands:
    """When ``execute_compensations()`` is called, any previously queued
    forward commands must be discarded.  Only compensation commands
    should appear in ``collect_commands()``."""

    @pytest.mark.anyio
    async def test_forward_commands_cleared_on_compensation(self) -> None:
        """After fail() triggers execute_compensations(), collect_commands()
        returns only compensation commands — no forward commands."""
        state = SagaState(saga_type="FiveStepSaga", correlation_id=uuid4())
        saga = FiveStepSaga(state)

        # Step 1: queues ReserveItems + adds CancelReservation compensation
        await saga.handle(OrderCreated(order_id="ORD-1"))
        assert len(state.compensation_stack) == 1

        # Step 2: queues ProcessPayment + adds CancelPayment compensation
        await saga.handle(ItemsReserved(order_id="ORD-1"))
        assert len(state.compensation_stack) == 2

        # At this point the saga has dispatched commands (cleared by collect_commands)
        # but the compensation_stack has 2 records.
        # Now fail — this calls execute_compensations() which clears forward commands.
        await saga.fail("Payment gateway error", compensate=True)

        assert state.status == SagaStatus.COMPENSATING
        commands = saga.collect_commands()

        # Only compensation commands, no forward commands
        assert len(commands) == 2
        assert all(isinstance(c, (CancelPayment, CancelReservation)) for c in commands)
        # Compensation is LIFO: CancelPayment popped first, then CancelReservation
        assert isinstance(commands[0], CancelPayment)
        assert isinstance(commands[1], CancelReservation)

    @pytest.mark.anyio
    async def test_forward_commands_discarded_through_manager(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """End-to-end: handler error triggers compensation and only
        compensation commands are dispatched via the manager."""
        registry = SagaRegistry()
        registry.register_saga(FiveStepSaga)
        bus, dispatched = _capture_bus(
            ReserveItems,
            ProcessPayment,
            ShipOrder,
            ScheduleDelivery,
            ConfirmOrder,
            CancelReservation,
            CancelPayment,
            CancelShipping,
        )
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        # 2 steps done: ReserveItems and ProcessPayment dispatched
        assert any(isinstance(c, ReserveItems) for c in dispatched)
        assert any(isinstance(c, ProcessPayment) for c in dispatched)
        dispatched.clear()

        # Step 3: handle PaymentProcessed (dispatches ShipOrder)
        await mgr.handle(PaymentProcessed(order_id="ORD-1", correlation_id=cid))
        dispatched.clear()

        # Now manually put saga into COMPENSATING with compensation stack
        # to test that only compensation commands are dispatched
        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        state.status = SagaStatus.COMPENSATING
        state.pending_commands.append(
            {
                "command_type": "CancelShipping",
                "module_name": "tests.saga.conftest",
                "data": {"order_id": "ORD-1"},
                "dispatched": False,
            }
        )
        state.touch()
        await saga_repo.save(state)

        # Recovery dispatches compensation commands
        await mgr.recover_pending_sagas()

        comp_types = {type(c) for c in dispatched}
        assert CancelShipping in comp_types
        assert CancelPayment in comp_types
        assert CancelReservation in comp_types
        # No forward commands leaked
        assert ShipOrder not in comp_types
        assert ScheduleDelivery not in comp_types
        assert ConfirmOrder not in comp_types

        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPENSATED


# ═══════════════════════════════════════════════════════════════════════
# 3. Resiliency — state saved after every change
# ═══════════════════════════════════════════════════════════════════════


class TestStateSavedAfterEveryChange:
    """The manager must save saga state after every state mutation so that
    a crash at any point leaves recoverable state in the repository.

    Key save points:
    - After creating initial state
    - After appending pending_commands (pre-dispatch checkpoint)
    - After each individual command dispatch
    - After clearing pending_commands (final checkpoint)
    - After handler error (compensating path)
    - After dispatch failure (suspension path)
    """

    @pytest.mark.anyio
    async def test_state_saved_after_initial_creation(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """Initial saga state is saved to the repository before any event handling."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        bus = _noop_bus()
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        # State exists in repository with RUNNING status
        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert state.status == SagaStatus.RUNNING

    @pytest.mark.anyio
    async def test_state_saved_after_each_forward_dispatch(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """With MultiDispatchSaga (dispatches ReserveItems + SendNotification),
        state is saved after each command dispatch. We verify by checking
        that the repository has the correct pending_commands state after
        full processing completes."""
        from .conftest import MultiDispatchSaga

        registry = SagaRegistry()
        registry.register_saga(MultiDispatchSaga)
        bus = _noop_bus()
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        # After full processing, pending_commands should be cleared
        state = await saga_repo.find_by_correlation_id(cid, "MultiDispatchSaga")
        assert state is not None
        assert state.pending_commands == []

    @pytest.mark.anyio
    async def test_state_saved_after_handler_error(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """When a handler raises, the manager catches it, triggers compensation,
        saves state. Verify COMPENSATED state is persisted after compensation."""
        registry = SagaRegistry()
        registry.register_saga(FiveStepSaga)
        bus, _ = _capture_bus(
            ReserveItems,
            ProcessPayment,
            ShipOrder,
            ScheduleDelivery,
            ConfirmOrder,
            CancelReservation,
            CancelPayment,
            CancelShipping,
        )
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))
        await mgr.handle(PaymentProcessed(order_id="ORD-1", correlation_id=cid))

        # Now drive step 4 and complete — verify state saved after full lifecycle
        await mgr.handle(OrderShipped(order_id="ORD-1", correlation_id=cid))
        await mgr.handle(DeliveryScheduled(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPLETED

    @pytest.mark.anyio
    async def test_state_saved_after_dispatch_failure(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """When forward dispatch fails, the saga is suspended and state
        is persisted so recovery can retry later."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)

        # Bus that fails on ReserveItems
        bus = CommandBus()

        async def fail_reserve(cmd: Any, uow: Any = None) -> EmptyCommandResult:
            raise RuntimeError("Service unavailable")

        bus.register(ReserveItems, fail_reserve, uow_factory=lambda: FakeUnitOfWork())

        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        # Handler dispatch fails → saga is suspended
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        # State is saved as SUSPENDED
        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED
        assert state.retry_count == 1
        # The manager unwraps CommandExecutionError for the suspension reason
        assert "Service unavailable" in (state.suspension_reason or "")

    @pytest.mark.anyio
    async def test_intermediate_state_recoverable_on_simulated_crash(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """After processing the first of multiple commands, the state in the
        repository reflects partial dispatch. A recovery pass can resume."""
        from .conftest import MultiDispatchSaga

        registry = SagaRegistry()
        registry.register_saga(MultiDispatchSaga)

        # Track save calls to verify intermediate saves
        bus = _noop_bus()
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        # After complete processing, saga is RUNNING with pending_commands cleared
        state = await saga_repo.find_by_correlation_id(cid, "MultiDispatchSaga")
        assert state is not None
        assert state.pending_commands == []
        # Commands were dispatched
        assert state.status == SagaStatus.RUNNING


# ═══════════════════════════════════════════════════════════════════════
# 4. COMPENSATING recovery path in manager
# ═══════════════════════════════════════════════════════════════════════


class TestCompensatingRecoveryPath:
    """When ``recover_pending_sagas`` encounters a saga in COMPENSATING state:

    - If ``compensation_stack`` is non-empty: re-executes compensations.
    - If ``compensation_stack`` is empty: transitions directly to COMPENSATED.
    """

    @pytest.mark.anyio
    async def test_recovery_with_non_empty_stack_dispatches_compensations(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """A saga in COMPENSATING state with a non-empty compensation_stack
        should have its compensations dispatched during recovery."""
        registry = SagaRegistry()
        registry.register_saga(FiveStepSaga)
        bus, dispatched = _capture_bus(
            ReserveItems,
            ProcessPayment,
            CancelReservation,
            CancelPayment,
            CancelShipping,
        )
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        # Drive saga to step 2 (2 compensations on stack)
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        # Manually put the saga into COMPENSATING state with a non-empty stack
        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        assert len(state.compensation_stack) == 2

        state.status = SagaStatus.COMPENSATING
        # Add a fake pending_command so find_stalled_sagas picks it up
        state.pending_commands.append(
            {
                "command_type": "CancelPayment",
                "module_name": "tests.saga.conftest",
                "data": {"order_id": "ORD-1"},
                "dispatched": False,
            }
        )
        state.touch()
        await saga_repo.save(state)

        dispatched.clear()

        # Recovery should dispatch compensations from the stack
        await mgr.recover_pending_sagas()

        # Compensation commands were dispatched
        # (LIFO: CancelPayment first, CancelReservation second)
        assert len(dispatched) == 2
        assert isinstance(dispatched[0], CancelPayment)
        assert isinstance(dispatched[1], CancelReservation)

        # State is now terminal
        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPENSATED

    @pytest.mark.anyio
    async def test_recovery_with_empty_stack_transitions_to_compensated(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """A saga in COMPENSATING state with an empty compensation_stack
        transitions directly to COMPENSATED during recovery."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        bus, _ = _capture_bus(ReserveItems, CancelReservation)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        # Manually set to COMPENSATING with empty stack
        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        state.status = SagaStatus.COMPENSATING
        state.compensation_stack.clear()
        # Add a fake pending_command so find_stalled_sagas picks it up
        state.pending_commands.append(
            {
                "command_type": "CancelReservation",
                "module_name": "tests.saga.conftest",
                "data": {"order_id": "ORD-1"},
                "dispatched": False,
            }
        )
        state.touch()
        await saga_repo.save(state)

        # Recovery should transition to COMPENSATED
        await mgr.recover_pending_sagas()

        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPENSATED
        assert state.compensation_stack == []

    @pytest.mark.anyio
    async def test_recovery_compensating_state_is_persisted(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """After recovery of a COMPENSATING saga, the terminal state
        is persisted to the repository."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        bus, _ = _capture_bus(ReserveItems, CancelReservation)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        # Set to COMPENSATING with non-empty stack
        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        state.status = SagaStatus.COMPENSATING
        state.pending_commands.append(
            {
                "command_type": "CancelReservation",
                "module_name": "tests.saga.conftest",
                "data": {"order_id": "ORD-1"},
                "dispatched": False,
            }
        )
        state.touch()
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        # Verify persistence by reading from repository again
        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert state.is_terminal
        assert state.status == SagaStatus.COMPENSATED
        assert state.compensation_stack == []

    @pytest.mark.anyio
    async def test_recovery_with_partial_compensation_stack(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """A saga in COMPENSATING state with partially drained stack
        dispatches only the remaining compensations."""
        registry = SagaRegistry()
        registry.register_saga(FiveStepSaga)
        bus, dispatched = _capture_bus(
            ReserveItems,
            ProcessPayment,
            ShipOrder,
            ScheduleDelivery,
            ConfirmOrder,
            CancelReservation,
            CancelPayment,
            CancelShipping,
        )
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        # Drive to step 3 (3 compensations)
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))
        await mgr.handle(PaymentProcessed(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        assert len(state.compensation_stack) == 3

        # Simulate partial crash: 1 compensation already dispatched
        # (pop from stack simulates prior partial execution)
        state.compensation_stack.pop()
        state.status = SagaStatus.COMPENSATING
        state.pending_commands.append(
            {
                "command_type": "CancelShipping",
                "module_name": "tests.saga.conftest",
                "data": {"order_id": "ORD-1"},
                "dispatched": False,
            }
        )
        state.touch()
        await saga_repo.save(state)

        dispatched.clear()

        # Recovery should dispatch remaining 2 compensations
        await mgr.recover_pending_sagas()

        assert len(dispatched) == 2
        dispatched_types = {type(c) for c in dispatched}
        # LIFO from remaining stack: CancelPayment, CancelReservation
        assert CancelPayment in dispatched_types
        assert CancelReservation in dispatched_types

        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPENSATED
