"""Recovery & error-path tests for SagaManager.

Covers the uncovered branches in manager.py (92.20% → target ≥97%):
- _recover_compensating_saga: unknown type skip, empty stack, non-empty stack
- _recover_failed_saga: compensation dispatch on max-retries
- _redispatch_undispatched: hydration failure, dispatch failure
- recover_pending_sagas: all-dispatched cleanup
- process_timeouts: on_timeout raises, force-fail, forward commands
- _handle_event_error: already-COMPENSATING path
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from pydomain.cqrs.command_bus import CommandBus
from pydomain.cqrs.commands import Command, EmptyCommandResult
from pydomain.cqrs.exceptions import CommandExecutionError
from pydomain.cqrs.saga.manager import SagaManager
from pydomain.cqrs.saga.registry import SagaRegistry
from pydomain.cqrs.saga.saga import Saga
from pydomain.cqrs.saga.state import (
    CompensationRecord,
    SagaState,
    SagaStatus,
)
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing import FakeUnitOfWork
from pydomain.testing.fake_saga_repository import FakeSagaRepository

from .conftest import (
    CancelReservation,
    ItemsReserved,
    OrderCreated,
    ReserveItems,
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
# 1A. _recover_compensating_saga — unknown saga type skip
# ═══════════════════════════════════════════════════════════════════════


class TestRecoverCompensatingUnknownType:
    """Unknown saga type in COMPENSATING state is skipped."""

    @pytest.mark.anyio
    async def test_unknown_saga_type_skipped_during_compensation_recovery(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Registry has no matching type → skip, no crash."""
        registry = SagaRegistry()
        bus = _noop_command_bus()
        mgr = _make_manager(saga_repo, registry, bus)

        state = SagaState(
            saga_type="NonExistentSaga",
            status=SagaStatus.COMPENSATING,
            correlation_id=uuid4(),
        )
        state.compensation_stack.append(
            CompensationRecord(
                command_type="CancelReservation",
                data={"order_id": "ORD-1"},
                description="Cancel",
                module_name="nonexistent.module",
            )
        )
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.status == SagaStatus.COMPENSATING
        assert len(updated.compensation_stack) == 1


# ═══════════════════════════════════════════════════════════════════════
# 1B. _recover_compensating_saga — empty compensation stack
# ═══════════════════════════════════════════════════════════════════════


class TestRecoverCompensatingEmptyStack:
    """COMPENSATING saga with empty stack transitions to COMPENSATED."""

    @pytest.mark.anyio
    async def test_empty_compensation_stack_becomes_compensated(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        bus, dispatched = _capture_bus(CancelReservation)
        mgr = _make_manager(saga_repo, registry, bus)

        state = SagaState(
            saga_type="TwoStepSaga",
            status=SagaStatus.COMPENSATING,
            correlation_id=uuid4(),
        )
        # Empty compensation_stack
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.status == SagaStatus.COMPENSATED
        assert len(dispatched) == 0


# ═══════════════════════════════════════════════════════════════════════
# 1B. _recover_compensating_saga — non-empty stack re-executes
# ═══════════════════════════════════════════════════════════════════════


class TestRecoverCompensatingNonEmptyStack:
    """COMPENSATING saga with non-empty stack re-dispatches compensations."""

    @pytest.mark.anyio
    async def test_non_empty_stack_redispatches_compensations(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        bus, dispatched = _capture_bus(CancelReservation)
        mgr = _make_manager(saga_repo, registry, bus)

        state = SagaState(
            saga_type="TwoStepSaga",
            status=SagaStatus.COMPENSATING,
            correlation_id=uuid4(),
        )
        state.compensation_stack.append(
            CompensationRecord(
                command_type="CancelReservation",
                data={"order_id": "ORD-1"},
                description="Cancel reservation",
                module_name=CancelReservation.__module__,
            )
        )
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.status == SagaStatus.COMPENSATED
        assert updated.compensation_stack == []
        assert len(dispatched) == 1
        assert isinstance(dispatched[0], CancelReservation)


# ═══════════════════════════════════════════════════════════════════════
# 1C. _recover_failed_saga — max retries with compensations
# ═══════════════════════════════════════════════════════════════════════


class TestRecoverFailedSaga:
    """Max retries exceeded triggers fail + optional compensation."""

    @pytest.mark.anyio
    async def test_max_retries_with_compensation_stack_triggers_compensating(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Saga with compensations at max-retries → COMPENSATING → COMPENSATED."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        bus, dispatched = _capture_bus(CancelReservation)
        mgr = _make_manager(saga_repo, registry, bus)

        state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=uuid4(),
            retry_count=3,
            max_retries=3,
        )
        state.pending_commands.append(
            {
                "command_type": "ReserveItems",
                "module_name": ReserveItems.__module__,
                "data": {"order_id": "ORD-1", "item_count": 5},
                "dispatched": False,
            }
        )
        state.compensation_stack.append(
            CompensationRecord(
                command_type="CancelReservation",
                data={"order_id": "ORD-1"},
                description="Cancel reservation",
                module_name=CancelReservation.__module__,
            )
        )
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.status == SagaStatus.COMPENSATED
        assert len(dispatched) == 1
        assert isinstance(dispatched[0], CancelReservation)

    @pytest.mark.anyio
    async def test_max_retries_without_compensation_goes_to_failed(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """No compensations → FAILED directly."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        bus = _noop_command_bus()
        mgr = _make_manager(saga_repo, registry, bus)

        state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=uuid4(),
            retry_count=3,
            max_retries=3,
        )
        state.pending_commands.append(
            {
                "command_type": "ReserveItems",
                "module_name": ReserveItems.__module__,
                "data": {"order_id": "ORD-1", "item_count": 5},
                "dispatched": False,
            }
        )
        # No compensation stack
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.status == SagaStatus.FAILED
        assert "max retries" in (updated.error or "").lower()


# ═══════════════════════════════════════════════════════════════════════
# 1D. _redispatch_undispatched — hydration failure
# ═══════════════════════════════════════════════════════════════════════


class TestRedispatchHydrationFailure:
    """Corrupt command data that fails hydration is skipped."""

    @pytest.mark.anyio
    async def test_hydration_returns_none_skips_command(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Command with bad module_name → hydration returns None → skip."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        bus, dispatched = _capture_bus(ReserveItems)
        mgr = _make_manager(saga_repo, registry, bus)

        state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=uuid4(),
            retry_count=1,
            max_retries=3,
        )
        # Bad command — hydration will return None
        state.pending_commands.append(
            {
                "command_type": "BadCommand",
                "module_name": "nonexistent.module",
                "data": {},
                "dispatched": False,
            }
        )
        # Good command alongside it
        state.pending_commands.append(
            {
                "command_type": "ReserveItems",
                "module_name": ReserveItems.__module__,
                "data": {"order_id": "ORD-1", "item_count": 5},
                "dispatched": False,
            }
        )
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        # The good command should have been dispatched
        assert any(isinstance(c, ReserveItems) for c in dispatched)

    @pytest.mark.anyio
    async def test_redispatch_failure_increments_retry(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Dispatch failure during re-dispatch increments retry_count."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        bus, _ = _failing_bus(ReserveItems)
        mgr = _make_manager(saga_repo, registry, bus)

        state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=uuid4(),
            retry_count=1,
            max_retries=5,
        )
        state.pending_commands.append(
            {
                "command_type": "ReserveItems",
                "module_name": ReserveItems.__module__,
                "data": {"order_id": "ORD-1", "item_count": 5},
                "dispatched": False,
            }
        )
        await saga_repo.save(state)

        with pytest.raises(CommandExecutionError, match="ReserveItems"):
            await mgr.recover_pending_sagas()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        # Outer recover_pending_sagas increments once, then
        # _redispatch_undispatched increments again on failure.
        assert updated.retry_count == 3


# ═══════════════════════════════════════════════════════════════════════
# 1E. process_timeouts — on_timeout raises
# ═══════════════════════════════════════════════════════════════════════


class TestProcessTimeoutsOnTimeoutRaises:
    """on_timeout() raising causes the saga to fail."""

    @pytest.mark.anyio
    async def test_on_timeout_exception_triggers_fail(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """on_timeout() raises → fail() called if not terminal."""

        class ExplodingTimeoutSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)

            async def _handle_event(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))

            async def on_timeout(self) -> None:
                raise RuntimeError("Timeout handler exploded")

        registry = SagaRegistry()
        registry.register_saga(ExplodingTimeoutSaga)
        bus = _noop_command_bus()
        mgr = _make_manager(saga_repo, registry, bus)

        state = SagaState(
            saga_type="ExplodingTimeoutSaga",
            correlation_id=uuid4(),
            status=SagaStatus.SUSPENDED,
            suspension_reason="waiting",
        )
        state.timeout_at = datetime.now(UTC) - timedelta(hours=1)
        state.suspended_at = datetime.now(UTC) - timedelta(hours=2)
        await saga_repo.save(state)

        await mgr.process_timeouts()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.status in (SagaStatus.FAILED, SagaStatus.COMPENSATING)


# ═══════════════════════════════════════════════════════════════════════
# 1E. process_timeouts — on_timeout doesn't resolve suspension
# ═══════════════════════════════════════════════════════════════════════


class TestProcessTimeoutsForceFail:
    """on_timeout() that doesn't resolve SUSPENDED triggers force-fail."""

    @pytest.mark.anyio
    async def test_on_timeout_leaves_suspended_force_fails(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """on_timeout() succeeds but saga remains SUSPENDED → force-fail."""

        class NoopTimeoutSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)

            async def _handle_event(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))

            async def on_timeout(self) -> None:
                pass  # Does nothing — saga stays SUSPENDED

        registry = SagaRegistry()
        registry.register_saga(NoopTimeoutSaga)
        bus = _noop_command_bus()
        mgr = _make_manager(saga_repo, registry, bus)

        state = SagaState(
            saga_type="NoopTimeoutSaga",
            correlation_id=uuid4(),
            status=SagaStatus.SUSPENDED,
            suspension_reason="waiting",
        )
        state.timeout_at = datetime.now(UTC) - timedelta(hours=1)
        state.suspended_at = datetime.now(UTC) - timedelta(hours=2)
        await saga_repo.save(state)

        await mgr.process_timeouts()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.status == SagaStatus.FAILED
        assert "did not resolve suspension" in (updated.error or "")


# ═══════════════════════════════════════════════════════════════════════
# 1E. process_timeouts — on_timeout triggers compensation
# ═══════════════════════════════════════════════════════════════════════


class TestProcessTimeoutsCompensation:
    """on_timeout() triggers compensation → COMPENSATING → COMPENSATED."""

    @pytest.mark.anyio
    async def test_timeout_triggers_compensation_dispatch(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Default on_timeout fails with compensation stack →
        compensations dispatched."""
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        bus, dispatched = _capture_bus(ReserveItems, CancelReservation)
        mgr = _make_manager(saga_repo, registry, bus)

        # Create a RUNNING saga with a compensation, then suspend it
        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert len(state.compensation_stack) == 1

        # Manually suspend with expired timeout
        state.status = SagaStatus.SUSPENDED
        state.suspended_at = datetime.now(UTC) - timedelta(hours=2)
        state.timeout_at = datetime.now(UTC) - timedelta(hours=1)
        state.suspension_reason = "waiting"
        await saga_repo.save(state)

        await mgr.process_timeouts()

        updated = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert updated is not None
        assert updated.status == SagaStatus.COMPENSATED
        assert any(isinstance(c, CancelReservation) for c in dispatched)


# ═══════════════════════════════════════════════════════════════════════
# 1H. process_timeouts — forward commands after timeout
# ═══════════════════════════════════════════════════════════════════════


class TestProcessTimeoutsForwardCommands:
    """on_timeout() that resumes and queues forward commands → dispatched."""

    @pytest.mark.anyio
    async def test_timeout_custom_recovery_dispatches_forward_commands(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """TimeoutRetrySaga resumes and dispatches a retry command."""

        class RetryTimeoutSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)

            async def _handle_event(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))

            async def on_timeout(self) -> None:
                self.resume()
                self.dispatch(ReserveItems(order_id="ORD-RETRY", item_count=1))

        registry = SagaRegistry()
        registry.register_saga(RetryTimeoutSaga)
        bus, dispatched = _capture_bus(ReserveItems)
        mgr = _make_manager(saga_repo, registry, bus)

        state = SagaState(
            saga_type="RetryTimeoutSaga",
            correlation_id=uuid4(),
            status=SagaStatus.SUSPENDED,
            suspension_reason="waiting",
        )
        state.timeout_at = datetime.now(UTC) - timedelta(hours=1)
        state.suspended_at = datetime.now(UTC) - timedelta(hours=2)
        await saga_repo.save(state)

        await mgr.process_timeouts()

        assert any(
            isinstance(c, ReserveItems) and c.order_id == "ORD-RETRY"
            for c in dispatched
        )

    @pytest.mark.anyio
    async def test_timeout_forward_dispatch_error_logged(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Forward command dispatch failure during timeout is logged, not raised."""

        class RetryTimeoutSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)

            async def _handle_event(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))

            async def on_timeout(self) -> None:
                self.resume()
                self.dispatch(ReserveItems(order_id="ORD-RETRY"))

        registry = SagaRegistry()
        registry.register_saga(RetryTimeoutSaga)
        bus, _ = _failing_bus(ReserveItems)
        mgr = _make_manager(saga_repo, registry, bus)

        state = SagaState(
            saga_type="RetryTimeoutSaga",
            correlation_id=uuid4(),
            status=SagaStatus.SUSPENDED,
            suspension_reason="waiting",
        )
        state.timeout_at = datetime.now(UTC) - timedelta(hours=1)
        state.suspended_at = datetime.now(UTC) - timedelta(hours=2)
        await saga_repo.save(state)

        # Should NOT raise — error is logged
        await mgr.process_timeouts()


# ═══════════════════════════════════════════════════════════════════════
# 1F. recover_pending_sagas — all dispatched cleanup
# ═══════════════════════════════════════════════════════════════════════


class TestRecoverAllDispatched:
    """Stalled saga where all commands are already dispatched gets cleaned up."""

    @pytest.mark.anyio
    async def test_all_dispatched_clears_pending_and_resets_retry(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        bus = _noop_command_bus()
        mgr = _make_manager(saga_repo, registry, bus)

        state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=uuid4(),
            retry_count=2,
        )
        state.pending_commands.append(
            {
                "command_type": "ReserveItems",
                "module_name": ReserveItems.__module__,
                "data": {"order_id": "ORD-1", "item_count": 5},
                "dispatched": True,
            }
        )
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.pending_commands == []
        assert updated.retry_count == 0


# ═══════════════════════════════════════════════════════════════════════
# 1G. _handle_event_error — saga already COMPENSATING
# ═══════════════════════════════════════════════════════════════════════


class TestHandleEventErrorAlreadyCompensating:
    """When saga is already COMPENSATING on handler error,
    dispatch compensations directly."""

    @pytest.mark.anyio
    async def test_already_compensating_dispatches_queued_compensations(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Handler error while COMPENSATING → dispatch queued compensations directly."""

        class CompensatingFailSaga(Saga[SagaState]):
            listens_to = [OrderCreated, ItemsReserved]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)
                self.on(ItemsReserved, handler=self._step2_fail)

            async def _step1(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")

            async def _step2_fail(self, event: DomainEvent) -> None:
                # Manually trigger compensating state before raising
                await self.execute_compensations()
                raise RuntimeError("Handler error while compensating")

        registry = SagaRegistry()
        registry.register_saga(CompensatingFailSaga)
        bus, dispatched = _capture_bus(ReserveItems, CancelReservation)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        updated = await saga_repo.find_by_correlation_id(cid, "CompensatingFailSaga")
        assert updated is not None
        # Compensations should have been dispatched
        assert any(isinstance(c, CancelReservation) for c in dispatched)


# ═══════════════════════════════════════════════════════════════════════
# 1E. process_timeouts — unknown saga type skip
# ═══════════════════════════════════════════════════════════════════════


class TestProcessTimeoutsUnknownType:
    """Unknown saga type during timeout processing is skipped."""

    @pytest.mark.anyio
    async def test_unknown_saga_type_skipped_during_timeout(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        registry = SagaRegistry()
        bus = _noop_command_bus()
        mgr = _make_manager(saga_repo, registry, bus)

        state = SagaState(
            saga_type="NonExistentSaga",
            correlation_id=uuid4(),
            status=SagaStatus.SUSPENDED,
            suspension_reason="waiting",
        )
        state.timeout_at = datetime.now(UTC) - timedelta(hours=1)
        state.suspended_at = datetime.now(UTC) - timedelta(hours=2)
        await saga_repo.save(state)

        # Should not raise
        await mgr.process_timeouts()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.status == SagaStatus.SUSPENDED
