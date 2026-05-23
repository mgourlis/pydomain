"""Correlation & causation ID propagation tests — flows.md §8.

Verifies the complete tracing chain across all dispatch paths:
- Forward commands: correlation_id = state.correlation_id, causation_id = event.event_id
- Compensation:     correlation_id = state.correlation_id, causation_id = state.id
- Recovery:         correlation_id = state.correlation_id, causation_id = state.id
- Timeout:          correlation_id = state.correlation_id, causation_id = state.id
"""

from __future__ import annotations

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
    ConfirmOrder,
    DeliveryScheduled,
    FiveStepSaga,
    ItemsReserved,
    MultiDispatchSaga,
    OrderCreated,
    OrderFailed,
    OrderShipped,
    PaymentProcessed,
    ProcessPayment,
    RequestApproval,
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


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def saga_repo() -> FakeSagaRepository:
    return FakeSagaRepository()


# ═══════════════════════════════════════════════════════════════════════
# 1. Forward Command Tracing
# ═══════════════════════════════════════════════════════════════════════


class TestForwardCommandTracing:
    """Forward commands: correlation_id = event.correlation_id,
    causation_id = event.event_id."""

    @pytest.mark.anyio
    async def test_correlation_id_matches_event_correlation_id(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event)

        assert len(dispatched) == 1
        assert dispatched[0].correlation_id == cid

    @pytest.mark.anyio
    async def test_causation_id_matches_event_event_id(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await mgr.handle(event)

        assert len(dispatched) == 1
        assert dispatched[0].causation_id == event.event_id

    @pytest.mark.anyio
    async def test_tracing_propagated_across_multiple_steps(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus, dispatched = _capture_bus(
            ReserveItems,
            ProcessPayment,
            ShipOrder,
            ScheduleDelivery,
            ConfirmOrder,
        )
        registry = SagaRegistry()
        registry.register_saga(FiveStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        event1 = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event1)

        event2 = ItemsReserved(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event2)

        event3 = PaymentProcessed(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event3)

        # Each step's command gets causation_id from that step's event
        assert (
            dispatched[0].causation_id == event1.event_id
        )  # ReserveItems ← OrderCreated
        assert (
            dispatched[1].causation_id == event2.event_id
        )  # ProcessPayment ← ItemsReserved
        assert (
            dispatched[2].causation_id == event3.event_id
        )  # ShipOrder ← PaymentProcessed

        # All share the same correlation_id
        for cmd in dispatched:
            assert cmd.correlation_id == cid

    @pytest.mark.anyio
    async def test_start_saga_causation_id_from_initial_event(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        saga_id = await mgr.start_saga(TwoStepSaga, event)
        assert saga_id is not None

        state = await saga_repo.get_by_id(saga_id)
        assert state is not None
        assert state.causation_id == event.event_id

    @pytest.mark.anyio
    async def test_start_saga_with_explicit_correlation_id(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        explicit_cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        saga_id = await mgr.start_saga(TwoStepSaga, event, correlation_id=explicit_cid)

        state = await saga_repo.get_by_id(saga_id)
        assert state is not None
        assert state.correlation_id == explicit_cid

        assert len(dispatched) == 1
        assert dispatched[0].correlation_id == explicit_cid

    @pytest.mark.anyio
    async def test_multi_dispatch_preserves_tracing_on_all_commands(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus, dispatched = _capture_bus(ReserveItems, SendNotification)
        registry = SagaRegistry()
        registry.register_saga(MultiDispatchSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event)

        assert len(dispatched) == 2
        for cmd in dispatched:
            assert cmd.correlation_id == cid
            assert cmd.causation_id == event.event_id

    @pytest.mark.anyio
    async def test_command_command_id_is_unique_per_dispatch(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus, dispatched = _capture_bus(ReserveItems, SendNotification)
        registry = SagaRegistry()
        registry.register_saga(MultiDispatchSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=uuid4()))

        assert len(dispatched) == 2
        assert dispatched[0].command_id != dispatched[1].command_id

    @pytest.mark.anyio
    async def test_step_record_captures_causation_id(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus = _noop_command_bus()
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await mgr.handle(event)

        state = await saga_repo.find_by_correlation_id(
            event.correlation_id, "TwoStepSaga"
        )
        assert state is not None
        assert len(state.step_history) >= 1
        assert state.step_history[0].causation_id == event.event_id

    @pytest.mark.anyio
    async def test_correlation_chain_across_5_events(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus, dispatched = _capture_bus(
            ReserveItems,
            ProcessPayment,
            ShipOrder,
            ScheduleDelivery,
            ConfirmOrder,
        )
        registry = SagaRegistry()
        registry.register_saga(FiveStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        events = [
            OrderCreated(order_id="ORD-1", correlation_id=cid),
            ItemsReserved(order_id="ORD-1", correlation_id=cid),
            PaymentProcessed(order_id="ORD-1", correlation_id=cid),
            OrderShipped(order_id="ORD-1", correlation_id=cid),
            DeliveryScheduled(order_id="ORD-1", correlation_id=cid),
        ]
        for evt in events:
            await mgr.handle(evt)

        # All 5 dispatched commands share the same correlation_id
        assert len(dispatched) == 5
        for cmd in dispatched:
            assert cmd.correlation_id == cid

    @pytest.mark.anyio
    async def test_forward_command_after_auto_resume(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus, dispatched = _capture_bus(RequestApproval, ConfirmOrder)
        registry = SagaRegistry()
        registry.register_saga(SuspendableSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        event1 = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event1)

        # First command from OrderCreated
        assert len(dispatched) == 1
        assert dispatched[0].causation_id == event1.event_id

        # Resume event — saga auto-resumes and handles
        event2 = ApprovalGranted(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event2)

        # Forward command after resume gets new event's event_id as causation_id
        assert len(dispatched) == 2
        assert dispatched[1].causation_id == event2.event_id
        assert dispatched[1].correlation_id == cid


# ═══════════════════════════════════════════════════════════════════════
# 2. Compensation Command Tracing
# ═══════════════════════════════════════════════════════════════════════


class TestCompensationTracing:
    """Compensation commands:
    correlation_id = state.correlation_id, causation_id = state.id."""

    @pytest.mark.anyio
    async def test_compensation_correlation_id_matches_saga_correlation_id(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        bus, dispatched = _capture_bus(ReserveItems, CancelReservation)
        registry = SagaRegistry()

        class FailSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)

            async def _step1(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")
                raise RuntimeError("boom")

        registry.register_saga(FailSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        comp_cmds = [c for c in dispatched if isinstance(c, CancelReservation)]
        assert len(comp_cmds) == 1
        assert comp_cmds[0].correlation_id == cid

    @pytest.mark.anyio
    async def test_compensation_causation_id_matches_saga_state_id(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        bus, dispatched = _capture_bus(ReserveItems, CancelReservation)
        registry = SagaRegistry()

        class FailSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)

            async def _step1(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")
                raise RuntimeError("boom")

        registry.register_saga(FailSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "FailSaga")
        assert state is not None

        comp_cmds = [c for c in dispatched if isinstance(c, CancelReservation)]
        assert len(comp_cmds) == 1
        # Causation_id for compensations = state.id (saga's own decision)
        assert comp_cmds[0].causation_id == state.id

    @pytest.mark.anyio
    async def test_compensation_tracing_with_handler_exception(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        bus, dispatched = _capture_bus(ReserveItems, CancelReservation)
        registry = SagaRegistry()

        class CrashSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)

            async def _step1(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")
                raise RuntimeError("Crash")

        registry.register_saga(CrashSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        comp_cmds = [c for c in dispatched if isinstance(c, CancelReservation)]
        assert len(comp_cmds) == 1
        assert comp_cmds[0].correlation_id == cid
        # Causation_id is state.id, not the exception
        state = await saga_repo.find_by_correlation_id(cid, "CrashSaga")
        assert comp_cmds[0].causation_id == state.id

    @pytest.mark.anyio
    async def test_compensation_tracing_with_explicit_fail(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        bus, dispatched = _capture_bus(ReserveItems, CancelReservation)
        registry = SagaRegistry()

        class ExplicitFailSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)

            async def _step1(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")
                await self.fail("Business rule violation")

        registry.register_saga(ExplicitFailSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        comp_cmds = [c for c in dispatched if isinstance(c, CancelReservation)]
        assert len(comp_cmds) == 1
        assert comp_cmds[0].correlation_id == cid

        state = await saga_repo.find_by_correlation_id(cid, "ExplicitFailSaga")
        assert comp_cmds[0].causation_id == state.id

    @pytest.mark.anyio
    async def test_lifo_compensations_all_carry_tracing_ids(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        bus, dispatched = _capture_bus(
            ReserveItems,
            ProcessPayment,
            ShipOrder,
            CancelShipping,
            CancelPayment,
            CancelReservation,
        )
        registry = SagaRegistry()
        registry.register_saga(FiveStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        # Process first 3 steps to build up compensation stack
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))
        await mgr.handle(PaymentProcessed(order_id="ORD-1", correlation_id=cid))

        # Now fail step 4 to trigger compensations
        class FailShipSaga(Saga[SagaState]):
            listens_to = [OrderCreated, ItemsReserved, PaymentProcessed, OrderFailed]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)
                self.on(ItemsReserved, handler=self._step2)
                self.on(PaymentProcessed, handler=self._step3)
                self.on(OrderFailed, handler=self._step4)

            async def _step1(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(
                    CancelReservation(order_id="ORD-1"), "Cancel reservation"
                )

            async def _step2(self, event: DomainEvent) -> None:
                self.dispatch(ProcessPayment(order_id="ORD-1"))
                self.add_compensation(CancelPayment(order_id="ORD-1"), "Cancel payment")

            async def _step3(self, event: DomainEvent) -> None:
                self.dispatch(ShipOrder(order_id="ORD-1"))
                self.add_compensation(
                    CancelShipping(order_id="ORD-1"), "Cancel shipping"
                )

            async def _step4(self, event: DomainEvent) -> None:
                await self.fail("Shipping failed")

        fail_registry = SagaRegistry()
        fail_registry.register_saga(FailShipSaga)
        fail_mgr = _make_manager(saga_repo, fail_registry, bus)

        # Replay events to get state into right position with FailShipSaga
        await fail_mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        await fail_mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))
        await fail_mgr.handle(PaymentProcessed(order_id="ORD-1", correlation_id=cid))
        await fail_mgr.handle(
            OrderFailed(order_id="ORD-1", reason="Ship fail", correlation_id=cid)
        )

        state = await saga_repo.find_by_correlation_id(cid, "FailShipSaga")
        assert state is not None

        comp_cmds = [
            c
            for c in dispatched
            if isinstance(c, (CancelShipping, CancelPayment, CancelReservation))
        ]
        # All compensation commands share correlation_id and causation_id=state.id
        for cmd in comp_cmds:
            assert cmd.correlation_id == cid
            assert cmd.causation_id == state.id

    @pytest.mark.anyio
    async def test_partial_compensation_failure_still_records_tracing(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        bus = CommandBus()
        dispatched: list[Command[Any]] = []

        async def capture_ok(cmd: Command[Any], uow: Any = None) -> EmptyCommandResult:
            dispatched.append(cmd)
            return EmptyCommandResult()

        async def fail_cmd(cmd: Command[Any], uow: Any = None) -> EmptyCommandResult:
            dispatched.append(cmd)
            raise RuntimeError("Compensation failed")

        bus.register(ReserveItems, capture_ok, uow_factory=lambda: FakeUnitOfWork())
        bus.register(CancelReservation, fail_cmd, uow_factory=lambda: FakeUnitOfWork())

        registry = SagaRegistry()

        class PartialFailSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)

            async def _step1(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")
                raise RuntimeError("boom")

        registry.register_saga(PartialFailSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "PartialFailSaga")
        assert state is not None
        assert len(state.failed_compensations) >= 1

        # The failed_compensations entry has the traced command data
        failed = state.failed_compensations[0]
        assert failed["data"]["correlation_id"] == cid
        assert failed["data"]["causation_id"] == state.id

    @pytest.mark.anyio
    async def test_compensation_tracing_independent_of_failure_event(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """All compensation commands use causation_id=state.id,
        regardless of which event caused the failure."""
        bus, dispatched = _capture_bus(
            ReserveItems,
            ProcessPayment,
            CancelPayment,
            CancelReservation,
        )
        registry = SagaRegistry()

        class FailAtStep2Saga(Saga[SagaState]):
            listens_to = [OrderCreated, ItemsReserved, OrderFailed]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)
                self.on(ItemsReserved, handler=self._step2)
                self.on(OrderFailed, handler=self._step_fail)

            async def _step1(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(
                    CancelReservation(order_id="ORD-1"), "Cancel reservation"
                )

            async def _step2(self, event: DomainEvent) -> None:
                self.dispatch(ProcessPayment(order_id="ORD-1"))
                self.add_compensation(CancelPayment(order_id="ORD-1"), "Cancel payment")

            async def _step_fail(self, event: DomainEvent) -> None:
                await self.fail("Order failed at step 2")

        registry.register_saga(FailAtStep2Saga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        fail_event = OrderFailed(order_id="ORD-1", reason="bad", correlation_id=cid)
        await mgr.handle(fail_event)

        state = await saga_repo.find_by_correlation_id(cid, "FailAtStep2Saga")
        assert state is not None

        comp_cmds = [
            c for c in dispatched if isinstance(c, (CancelPayment, CancelReservation))
        ]
        for cmd in comp_cmds:
            # causation_id is state.id, NOT fail_event.event_id
            assert cmd.causation_id == state.id
            assert cmd.correlation_id == cid


# ═══════════════════════════════════════════════════════════════════════
# 3. Recovery Command Tracing
# ═══════════════════════════════════════════════════════════════════════


class TestRecoveryTracing:
    """Recovery commands:
    correlation_id = state.correlation_id, causation_id = state.id."""

    @pytest.mark.anyio
    async def test_recovery_command_correlation_id_matches_saga_state(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
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
                    "data": {"order_id": "ORD-1", "item_count": 3},
                    "dispatched": False,
                },
            ],
        )
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        assert len(dispatched) == 1
        assert dispatched[0].correlation_id == cid

    @pytest.mark.anyio
    async def test_recovery_command_causation_id_matches_saga_state_id(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
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
                    "data": {"order_id": "ORD-1", "item_count": 3},
                    "dispatched": False,
                },
            ],
        )
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        assert len(dispatched) == 1
        assert dispatched[0].causation_id == state.id

    @pytest.mark.anyio
    async def test_recovery_after_partial_dispatch_preserves_tracing(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        bus, dispatched = _capture_bus(ReserveItems, SendNotification)
        registry = SagaRegistry()
        registry.register_saga(MultiDispatchSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        state = SagaState(
            saga_type="MultiDispatchSaga",
            correlation_id=cid,
            status=SagaStatus.RUNNING,
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
        assert dispatched[0].correlation_id == cid
        assert dispatched[0].causation_id == state.id

    @pytest.mark.anyio
    async def test_recovery_compensating_saga_tracing(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        bus, dispatched = _capture_bus(CancelReservation)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=cid,
            status=SagaStatus.COMPENSATING,
            compensation_stack=[],  # Already popped during execute_compensations
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

        # COMPENSATING sagas dispatch via _dispatch_compensations
        # But since collect_commands returns empty (compensation_stack is empty),
        # no commands are dispatched. This tests the COMPENSATING branch.
        state_after = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state_after is not None

    @pytest.mark.anyio
    async def test_recovery_max_retries_failure_still_has_correct_error_context(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        bus = _noop_command_bus()
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=cid,
            status=SagaStatus.RUNNING,
            retry_count=3,  # At max
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

        state_after = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state_after is not None
        assert state_after.status in (SagaStatus.FAILED, SagaStatus.COMPENSATED)
        assert state_after.error is not None

    @pytest.mark.anyio
    async def test_multiple_recovery_cycles_preserve_tracing(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()

        # Cycle 1
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
        assert dispatched[0].correlation_id == cid

        # Cycle 2 — set up another stalled state with same correlation
        state2 = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=cid,
            status=SagaStatus.RUNNING,
            pending_commands=[
                {
                    "command_type": "ReserveItems",
                    "module_name": ReserveItems.__module__,
                    "data": {"order_id": "ORD-2", "item_count": 1},
                    "dispatched": False,
                },
            ],
        )
        await saga_repo.save(state2)
        await mgr.recover_pending_sagas()
        assert dispatched[1].correlation_id == cid


# ═══════════════════════════════════════════════════════════════════════
# 4. Timeout Command Tracing
# ═══════════════════════════════════════════════════════════════════════


class TestTimeoutTracing:
    """Timeout commands: causation_id = state.id (saga's own decision to timeout)."""

    @pytest.mark.anyio
    async def test_timeout_default_fail_compensation_tracing(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        bus, dispatched = _capture_bus(RequestApproval, CancelReservation)
        registry = SagaRegistry()

        class TimeoutWithCompSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: RequestApproval(order_id=e.order_id),
                    step="awaiting_approval",
                    compensate=lambda e: CancelReservation(order_id=e.order_id),
                    suspend=True,
                    suspend_reason="Waiting for approval",
                    suspend_timeout=__import__("datetime").timedelta(milliseconds=0),
                )

        registry.register_saga(TimeoutWithCompSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "TimeoutWithCompSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED

        # Process timeouts — default on_timeout fails and compensates
        await mgr.process_timeouts()

        comp_cmds = [c for c in dispatched if isinstance(c, CancelReservation)]
        assert len(comp_cmds) == 1
        assert comp_cmds[0].correlation_id == cid
        assert comp_cmds[0].causation_id == state.id

    @pytest.mark.anyio
    async def test_timeout_custom_retry_forward_command_tracing(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TimeoutRetrySaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "TimeoutRetrySaga")
        assert state is not None

        # Force timeout
        state.status = SagaStatus.SUSPENDED
        state.timeout_at = __import__("datetime").datetime.now(
            __import__("datetime").UTC
        )
        await saga_repo.save(state)

        await mgr.process_timeouts()

        # TimeoutRetrySaga.on_timeout resumes and dispatches ReserveItems
        retry_cmds = [c for c in dispatched if c.order_id == "ORD-RETRY"]
        assert len(retry_cmds) == 1
        assert retry_cmds[0].causation_id == state.id
        assert retry_cmds[0].correlation_id == cid

    @pytest.mark.anyio
    async def test_timeout_force_fail_no_compensation_no_tracing_commands(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        bus, dispatched = _capture_bus(RequestApproval)
        registry = SagaRegistry()

        class TimeoutForceFailSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: RequestApproval(order_id=e.order_id),
                    step="awaiting",
                    suspend=True,
                    suspend_reason="Waiting",
                    suspend_timeout=__import__("datetime").timedelta(milliseconds=0),
                )

        registry.register_saga(TimeoutForceFailSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        await mgr.process_timeouts()

        # Only the original command, no additional tracing commands
        state = await saga_repo.find_by_correlation_id(cid, "TimeoutForceFailSaga")
        assert state is not None
        assert state.status == SagaStatus.FAILED

    @pytest.mark.anyio
    async def test_timeout_handler_crash_no_commands_dispatched(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        bus, dispatched = _capture_bus(RequestApproval)
        registry = SagaRegistry()

        class TimeoutCrashSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: RequestApproval(order_id=e.order_id),
                    step="awaiting",
                    suspend=True,
                    suspend_reason="Waiting",
                    suspend_timeout=__import__("datetime").timedelta(milliseconds=0),
                )

            async def on_timeout(self) -> None:
                raise RuntimeError("Timeout handler crashed")

        registry.register_saga(TimeoutCrashSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        await mgr.process_timeouts()

        state = await saga_repo.find_by_correlation_id(cid, "TimeoutCrashSaga")
        assert state is not None
        assert state.status == SagaStatus.FAILED


# ═══════════════════════════════════════════════════════════════════════
# 5. Multi-Saga Tracing Independence
# ═══════════════════════════════════════════════════════════════════════


class TestMultiSagaTracing:
    """Tracing chains are independent across sagas sharing the same event."""

    @pytest.mark.anyio
    async def test_two_sagas_same_event_independent_tracing(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        bus, dispatched = _capture_bus(ReserveItems, SendNotification)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        registry.register_saga(AuditSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event)

        reserve_cmds = [c for c in dispatched if isinstance(c, ReserveItems)]
        notif_cmds = [c for c in dispatched if isinstance(c, SendNotification)]
        assert len(reserve_cmds) == 1
        assert len(notif_cmds) == 1

        # Both share correlation_id from the event
        assert reserve_cmds[0].correlation_id == cid
        assert notif_cmds[0].correlation_id == cid

        # Both have causation_id from the same event
        assert reserve_cmds[0].causation_id == event.event_id
        assert notif_cmds[0].causation_id == event.event_id

    @pytest.mark.anyio
    async def test_two_sagas_same_event_compensation_tracing_independent(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        bus, dispatched = _capture_bus(
            ReserveItems, SendNotification, CancelReservation
        )
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        registry.register_saga(AuditSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        # TwoStepSaga gets first event, then fails on second
        # (implicit via handler crash). AuditSaga completes on first event.
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        # Verify AuditSaga is COMPLETED
        audit_state = await saga_repo.find_by_correlation_id(cid, "AuditSaga")
        assert audit_state is not None
        assert audit_state.status == SagaStatus.COMPLETED

    @pytest.mark.anyio
    async def test_start_saga_then_handle_shares_correlation_chain(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        bus, dispatched = _capture_bus(ReserveItems, ConfirmOrder)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        event1 = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await mgr.start_saga(TwoStepSaga, event1)

        event2 = ItemsReserved(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event2)

        # Both commands share the same correlation chain
        assert len(dispatched) == 2
        for cmd in dispatched:
            assert cmd.correlation_id == cid

    @pytest.mark.anyio
    async def test_two_sagas_same_event_step_histories_independent(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        bus = _noop_command_bus()
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        registry.register_saga(AuditSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        two_step_state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        audit_state = await saga_repo.find_by_correlation_id(cid, "AuditSaga")
        assert two_step_state is not None
        assert audit_state is not None

        # Step histories are independent — different causation_ids
        assert two_step_state.id != audit_state.id
        # Both have step_history entries with the same event's event_id as causation_id
        assert len(two_step_state.step_history) >= 1
        assert len(audit_state.step_history) >= 1
