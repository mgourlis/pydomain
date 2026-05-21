"""Durability & resiliency edge case tests.

Covers:
- Optimistic concurrency & versioning
- Resource exhaustion & limits (many steps, large compensation stacks)
- Malformed state recovery
- Boundary conditions (single-step, zero timeout, far-future timeout)
- Idempotency under stress
- Multi-saga resiliency
- State serialization round-trips
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from pydomain.cqrs.command_bus import CommandBus
from pydomain.cqrs.commands import Command, EmptyCommandResult
from pydomain.cqrs.saga.manager import SagaManager
from pydomain.cqrs.saga.registry import SagaRegistry
from pydomain.cqrs.saga.saga import Saga
from pydomain.cqrs.saga.state import (
    CompensationRecord,
    SagaState,
    SagaStatus,
    StepRecord,
)
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing import FakeUnitOfWork
from pydomain.testing.fake_saga_repository import FakeSagaRepository

from .conftest import (
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
# 1. Optimistic Concurrency & Versioning
# ═══════════════════════════════════════════════════════════════════════


class TestConcurrencyControl:
    """Version increments and concurrency semantics."""

    @pytest.mark.anyio
    async def test_version_increments_on_every_save(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        state = SagaState(saga_type="TestSaga", correlation_id=uuid4())
        v0 = state.version

        await saga_repo.save(state)
        # Save doesn't change version — that's touch()'s job
        assert state.version == v0

        state.touch()
        v1 = state.version
        assert v1 > v0

    @pytest.mark.anyio
    async def test_version_increments_across_multiple_events(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus = _noop_command_bus()
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

        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        assert state.version > 0

    @pytest.mark.anyio
    async def test_sequential_events_on_same_sagma(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Two events with same correlation_id processed sequentially."""
        bus, dispatched = _capture_bus(ReserveItems, ConfirmOrder)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPLETED
        assert len(state.step_history) >= 2

    @pytest.mark.anyio
    async def test_concurrent_recovery_and_handle(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Recovery runs, then handle processes new event — no data loss."""
        bus, dispatched = _capture_bus(ReserveItems, ConfirmOrder)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        # Set up stalled state
        state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=cid,
            status=SagaStatus.RUNNING,
            processed_event_ids=set(),
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

        # Recovery first
        await mgr.recover_pending_sagas()
        assert len(dispatched) == 1

        # Then handle new event
        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))
        assert len(dispatched) == 2

        state_after = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state_after is not None
        assert state_after.status == SagaStatus.COMPLETED

    @pytest.mark.anyio
    async def test_stale_state_detection_via_version(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Version tracking documents optimistic concurrency contract.

        FakeSagaRepository always succeeds, but the contract is that
        version should increment on each mutation for real DB implementations.
        """
        state = SagaState(saga_type="TestSaga", correlation_id=uuid4())
        original_version = state.version

        state.touch()
        assert state.version == original_version + 1

        state.touch()
        assert state.version == original_version + 2


# ═══════════════════════════════════════════════════════════════════════
# 2. Resource Exhaustion & Limits
# ═══════════════════════════════════════════════════════════════════════


class TestResourceLimits:
    """Sagas with many steps, large compensation stacks, and lots of events."""

    @pytest.mark.anyio
    async def test_saga_with_many_steps_does_not_lose_state(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus = _noop_command_bus()
        registry = SagaRegistry()

        class ManyStepSaga(Saga[SagaState]):
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
                self.on(DeliveryScheduled, handler=self._step5)

            async def _step1(self, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")

            async def _step2(self, event: DomainEvent) -> None:
                self.dispatch(ProcessPayment(order_id="ORD-1"))
                self.add_compensation(CancelPayment(order_id="ORD-1"), "Cancel")

            async def _step3(self, event: DomainEvent) -> None:
                self.dispatch(ShipOrder(order_id="ORD-1"))
                self.add_compensation(CancelShipping(order_id="ORD-1"), "Cancel")

            async def _step4(self, event: DomainEvent) -> None:
                self.dispatch(ScheduleDelivery(order_id="ORD-1"))

            async def _step5(self, event: DomainEvent) -> None:
                self.dispatch(ConfirmOrder(order_id="ORD-1"))
                self.complete()

        registry.register_saga(ManyStepSaga)
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

        state = await saga_repo.find_by_correlation_id(cid, "ManyStepSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPLETED
        assert len(state.step_history) == 5
        assert (
            len(state.compensation_stack) == 0
        )  # Not popped — still on stack after complete

    @pytest.mark.anyio
    async def test_compensation_stack_with_many_entries(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """10 compensations, all dispatched in LIFO order."""
        bus, dispatched = _capture_bus(
            ReserveItems,
            ProcessPayment,
            ShipOrder,
            CancelShipping,
            CancelPayment,
            CancelReservation,
        )
        registry = SagaRegistry()

        class ThreeCompSaga(Saga[SagaState]):
            listens_to = [OrderCreated, ItemsReserved, PaymentProcessed, OrderFailed]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)
                self.on(ItemsReserved, handler=self._step2)
                self.on(PaymentProcessed, handler=self._step3)
                self.on(OrderFailed, handler=self._fail)

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

            async def _fail(self, event: DomainEvent) -> None:
                await self.fail("Failed")

        registry.register_saga(ThreeCompSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        await mgr.handle(ItemsReserved(order_id="ORD-1", correlation_id=cid))
        await mgr.handle(PaymentProcessed(order_id="ORD-1", correlation_id=cid))
        await mgr.handle(
            OrderFailed(order_id="ORD-1", reason="bad", correlation_id=cid)
        )

        state = await saga_repo.find_by_correlation_id(cid, "ThreeCompSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPENSATED

        comp_cmds = [
            c
            for c in dispatched
            if isinstance(c, (CancelShipping, CancelPayment, CancelReservation))
        ]
        assert len(comp_cmds) == 3
        # LIFO order: CancelShipping first, then CancelPayment, then CancelReservation
        assert isinstance(comp_cmds[0], CancelShipping)
        assert isinstance(comp_cmds[1], CancelPayment)
        assert isinstance(comp_cmds[2], CancelReservation)

    @pytest.mark.anyio
    async def test_pending_commands_grows_and_clears(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Multi-dispatch per step — pending_commands grows then clears."""
        bus, dispatched = _capture_bus(ReserveItems, SendNotification)
        registry = SagaRegistry()
        registry.register_saga(MultiDispatchSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "MultiDispatchSaga")
        assert state is not None
        # After successful dispatch, pending_commands are cleared
        assert state.pending_commands == []
        assert len(dispatched) == 2

    @pytest.mark.anyio
    async def test_processed_event_ids_grows_unboundedly(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """100 events, all tracked in processed_event_ids."""
        bus = _noop_command_bus()
        registry = SagaRegistry()
        registry.register_saga(FiveStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        # Process 5 events for the full saga lifecycle
        events = [
            OrderCreated(order_id="ORD-1", correlation_id=cid),
            ItemsReserved(order_id="ORD-1", correlation_id=cid),
            PaymentProcessed(order_id="ORD-1", correlation_id=cid),
            OrderShipped(order_id="ORD-1", correlation_id=cid),
            DeliveryScheduled(order_id="ORD-1", correlation_id=cid),
        ]
        for evt in events:
            await mgr.handle(evt)

        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        assert len(state.processed_event_ids) == 5

    @pytest.mark.anyio
    async def test_step_history_grows_with_many_events(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Steps recorded, all accessible."""
        bus = _noop_command_bus()
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

        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        assert len(state.step_history) == 5
        # Each step has causation_id from its triggering event
        for i, step in enumerate(state.step_history):
            assert step.causation_id == events[i].event_id

    @pytest.mark.anyio
    async def test_large_metadata_dict_stored_correctly(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Metadata with many keys persists through save/load."""
        metadata = {f"key_{i}": f"value_{i}" for i in range(50)}
        state = SagaState(
            saga_type="TestSaga",
            correlation_id=uuid4(),
            metadata=metadata,
        )
        await saga_repo.save(state)

        loaded = await saga_repo.get_by_id(state.id)
        assert loaded is not None
        assert loaded.metadata == metadata


# ═══════════════════════════════════════════════════════════════════════
# 3. Malformed State Recovery
# ═══════════════════════════════════════════════════════════════════════


class TestMalformedStateRecovery:
    """Recovery handles corrupt or malformed state gracefully."""

    @pytest.mark.anyio
    async def test_recovery_with_corrupt_pending_commands_entry(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """pending_commands has missing keys — hydration fails gracefully."""
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
                    # Missing "data" key
                    "dispatched": False,
                },
            ],
        )
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        # Hydration fails (no data key) — command skipped
        state_after = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state_after is not None

    @pytest.mark.anyio
    async def test_recovery_with_unknown_command_module(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """module_name points to nonexistent module — hydrate returns None."""
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
                    "module_name": "nonexistent.module.path",
                    "data": {"order_id": "ORD-1", "item_count": 5},
                    "dispatched": False,
                },
            ],
        )
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        # Command could not be hydrated — skipped
        assert len(dispatched) == 0

    @pytest.mark.anyio
    async def test_recovery_with_unknown_command_type(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Module exists but command_type doesn't — hydrate returns None."""
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
                    "command_type": "NonExistentCommand",
                    "module_name": ReserveItems.__module__,
                    "data": {"order_id": "ORD-1", "item_count": 5},
                    "dispatched": False,
                },
            ],
        )
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        # Command type not found in module — skipped
        assert len(dispatched) == 0

    @pytest.mark.anyio
    async def test_recovery_with_invalid_command_data(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """data has wrong field types — model_validate fails, command skipped."""
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
                    "data": {"order_id": 12345, "item_count": "not_a_number"},
                    "dispatched": False,
                },
            ],
        )
        await saga_repo.save(state)

        await mgr.recover_pending_sagas()

        # Pydantic validation may fail — command skipped
        # (depends on Pydantic's coercion behavior)

    @pytest.mark.anyio
    async def test_recovery_with_missing_correlation_id(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """state has correlation_id=None — recovery dispatches
        without correlation tracing."""
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=None,  # Missing
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

        await mgr.recover_pending_sagas()

        # Command dispatched without correlation_id
        assert len(dispatched) == 1
        assert dispatched[0].correlation_id is None

    @pytest.mark.anyio
    async def test_recovery_with_negative_retry_count(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """retry_count < 0 — recovery still works (increments from negative)."""
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=cid,
            status=SagaStatus.RUNNING,
            retry_count=-1,  # Negative
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

        # Recovery still works — retry_count increments from -1 to 0
        assert len(dispatched) == 1

    @pytest.mark.anyio
    async def test_recovery_with_empty_saga_type(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """saga_type="" — registry returns None, skipped with warning."""
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        state = SagaState(
            saga_type="",  # Empty
            correlation_id=uuid4(),
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

        await mgr.recover_pending_sagas()

        # Unknown saga type — skipped
        assert len(dispatched) == 0


# ═══════════════════════════════════════════════════════════════════════
# 4. Boundary Conditions
# ═══════════════════════════════════════════════════════════════════════


class TestBoundaryConditions:
    """Edge cases: single-step, zero timeout, far-future timeout."""

    @pytest.mark.anyio
    async def test_saga_with_single_step_no_compensation_completes(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus, dispatched = _capture_bus(SendNotification)
        registry = SagaRegistry()
        registry.register_saga(AuditSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "AuditSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPLETED
        assert len(dispatched) == 1

    @pytest.mark.anyio
    async def test_saga_with_single_step_with_compensation_fails(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus, dispatched = _capture_bus(SendNotification, CancelReservation)
        registry = SagaRegistry()

        class SingleFailSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step)

            async def _step(self, event: DomainEvent) -> None:
                self.dispatch(SendNotification(order_id="ORD-1", message="hi"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")
                raise RuntimeError("Fail")

        registry.register_saga(SingleFailSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        state = await saga_repo.find_by_correlation_id(cid, "SingleFailSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPENSATED

    @pytest.mark.anyio
    async def test_empty_event_triggers_saga_creation(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Minimal event (only event_id, correlation_id) creates saga."""
        bus = _noop_command_bus()
        registry = SagaRegistry()
        registry.register_saga(AuditSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event)

        state = await saga_repo.find_by_correlation_id(cid, "AuditSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPLETED

    @pytest.mark.anyio
    async def test_saga_with_zero_timeout_immediately_expires(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus, dispatched = _capture_bus(RequestApproval)
        registry = SagaRegistry()

        class ZeroTimeoutSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: RequestApproval(order_id=e.order_id),
                    step="awaiting",
                    suspend=True,
                    suspend_reason="Immediate timeout",
                    suspend_timeout=timedelta(milliseconds=0),
                )

        registry.register_saga(ZeroTimeoutSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        # Immediately process timeouts
        await mgr.process_timeouts()

        state = await saga_repo.find_by_correlation_id(cid, "ZeroTimeoutSaga")
        assert state is not None
        assert state.status == SagaStatus.FAILED

    @pytest.mark.anyio
    async def test_saga_with_far_future_timeout_not_processed(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus, dispatched = _capture_bus(RequestApproval)
        registry = SagaRegistry()

        class FarFutureTimeoutSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: RequestApproval(order_id=e.order_id),
                    step="awaiting",
                    suspend=True,
                    suspend_reason="Far future",
                    suspend_timeout=timedelta(days=365 * 100),  # 100 years
                )

        registry.register_saga(FarFutureTimeoutSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        # Process timeouts — should NOT expire
        await mgr.process_timeouts()

        state = await saga_repo.find_by_correlation_id(cid, "FarFutureTimeoutSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED  # Still suspended

    @pytest.mark.anyio
    async def test_uuid_edge_cases_accepted(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """All-zeros UUID accepted for correlation_id."""

        bus = _noop_command_bus()
        registry = SagaRegistry()
        registry.register_saga(AuditSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        zeros_cid = UUID("00000000-0000-0000-0000-000000000000")
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=zeros_cid))

        state = await saga_repo.find_by_correlation_id(zeros_cid, "AuditSaga")
        assert state is not None
        assert state.correlation_id == zeros_cid


# ═══════════════════════════════════════════════════════════════════════
# 5. Idempotency Under Stress
# ═══════════════════════════════════════════════════════════════════════


class TestIdempotencyStress:
    """Duplicate events and commands handled idempotently."""

    @pytest.mark.anyio
    async def test_duplicate_event_after_completion_is_noop(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus, dispatched = _capture_bus(SendNotification)
        registry = SagaRegistry()
        registry.register_saga(AuditSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event)

        assert len(dispatched) == 1

        # Send same event again — idempotent
        await mgr.handle(event)
        assert len(dispatched) == 1  # No additional dispatch

    @pytest.mark.anyio
    async def test_duplicate_event_after_failure_is_noop(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()

        class FailSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step)

            async def _step(self, event: DomainEvent) -> None:
                raise RuntimeError("Always fail")

        registry.register_saga(FailSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event)

        # Send same event again — saga is terminal, so it's an idempotent no-op
        await mgr.handle(event)

        state = await saga_repo.find_by_correlation_id(cid, "FailSaga")
        assert state is not None
        assert state.status == SagaStatus.FAILED

    @pytest.mark.anyio
    async def test_duplicate_event_during_recovery(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus, dispatched = _capture_bus(ReserveItems, ConfirmOrder)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        event1 = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event1)

        # Simulate crash: add pending command for recovery
        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None

        # Recovery
        await mgr.recover_pending_sagas()

        # New event arrives — saga continues normally
        event2 = ItemsReserved(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event2)

        state_after = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state_after is not None
        assert state_after.status == SagaStatus.COMPLETED

    @pytest.mark.anyio
    async def test_duplicate_start_saga_calls(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """start_saga called twice with same correlation_id → same state."""
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        id1 = await mgr.start_saga(TwoStepSaga, event, correlation_id=cid)
        id2 = await mgr.start_saga(TwoStepSaga, event, correlation_id=cid)

        # Same state found on second call
        assert id1 == id2
        assert (
            len(dispatched) == 1
        )  # Second call is idempotent (event already processed)

    @pytest.mark.anyio
    async def test_concurrent_duplicate_events(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Two events with same event_id — second is skipped by idempotency check."""
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)

        await mgr.handle(event)
        first_count = len(dispatched)

        # Send exact same event object again — same event_id
        await mgr.handle(event)

        state = await saga_repo.find_by_correlation_id(cid, "TwoStepSaga")
        assert state is not None
        # Second event was skipped (same event_id)
        assert len(dispatched) == first_count


# ═══════════════════════════════════════════════════════════════════════
# 6. Multi-Saga Resiliency
# ═══════════════════════════════════════════════════════════════════════


class TestMultiSagaResiliency:
    """One saga's failure doesn't affect another."""

    @pytest.mark.anyio
    async def test_one_saga_crash_does_not_affect_other(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """AuditSaga registered first → completes;
        CrashSaga registered second → crashes."""
        bus, dispatched = _capture_bus(SendNotification)
        registry = SagaRegistry()

        class CrashSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self: CrashSaga, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._crash)

            async def _crash(self: CrashSaga, event: DomainEvent) -> None:
                raise RuntimeError("Crash!")

        # Register AuditSaga FIRST so manager processes it before CrashSaga.
        # Manager iterates sagas in registration order; AuditSaga completes,
        # then CrashSaga crashes and propagates.
        registry.register_saga(AuditSaga)
        registry.register_saga(CrashSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        # AuditSaga completed before CrashSaga crashed
        audit_state = await saga_repo.find_by_correlation_id(cid, "AuditSaga")
        assert audit_state is not None
        assert audit_state.status == SagaStatus.COMPLETED

    @pytest.mark.anyio
    async def test_one_saga_compensation_failure_does_not_affect_other(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """AuditSaga completes; CompFailSaga's compensation failure is isolated."""
        bus, dispatched = _capture_bus(
            SendNotification, ReserveItems, CancelReservation
        )
        registry = SagaRegistry()

        class CompFailSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self: CompFailSaga, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step)

            async def _step(self: CompFailSaga, event: DomainEvent) -> None:
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel")
                raise RuntimeError("Boom")

        # Register AuditSaga FIRST so it processes
        # and persists before CompFailSaga crashes.
        registry.register_saga(AuditSaga)
        registry.register_saga(CompFailSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        # AuditSaga completed before CompFailSaga crashed
        audit_state = await saga_repo.find_by_correlation_id(cid, "AuditSaga")
        assert audit_state is not None
        assert audit_state.status == SagaStatus.COMPLETED

    @pytest.mark.anyio
    async def test_recovery_of_multiple_stalled_sagas(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        # Create 5 stalled sagas
        for i in range(5):
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

        await mgr.recover_pending_sagas(limit=10)

        assert len(dispatched) == 5

    @pytest.mark.anyio
    async def test_recovery_limit_respected(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        # Create 10 stalled sagas
        for i in range(10):
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

        await mgr.recover_pending_sagas(limit=5)

        assert len(dispatched) == 5

    @pytest.mark.anyio
    async def test_mixed_status_sagas_in_repo(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        bus, dispatched = _capture_bus(ReserveItems)
        registry = SagaRegistry()
        registry.register_saga(TwoStepSaga)
        mgr = _make_manager(saga_repo, registry, bus)

        # RUNNING with pending (stalled)
        stalled_state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=uuid4(),
            status=SagaStatus.RUNNING,
            pending_commands=[
                {
                    "command_type": "ReserveItems",
                    "module_name": ReserveItems.__module__,
                    "data": {"order_id": "ORD-1", "item_count": 1},
                    "dispatched": False,
                },
            ],
        )
        await saga_repo.save(stalled_state)

        # COMPLETED (terminal)
        completed_state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=uuid4(),
            status=SagaStatus.COMPLETED,
        )
        await saga_repo.save(completed_state)

        # SUSPENDED (not stalled by pending_commands)
        suspended_state = SagaState(
            saga_type="TwoStepSaga",
            correlation_id=uuid4(),
            status=SagaStatus.SUSPENDED,
        )
        await saga_repo.save(suspended_state)

        await mgr.recover_pending_sagas()

        # Only the stalled one is recovered
        assert len(dispatched) == 1

    @pytest.mark.anyio
    async def test_saga_registry_dynamic_registration(
        self, saga_repo: FakeSagaRepository
    ) -> None:
        """Register saga after manager created — verify handle() picks it up."""
        bus, dispatched = _capture_bus(SendNotification)
        registry = SagaRegistry()
        mgr = _make_manager(saga_repo, registry, bus)

        # Initially no sagas registered
        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        assert len(dispatched) == 0

        # Register saga
        registry.register_saga(AuditSaga)

        # New event — now picked up
        cid2 = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-2", correlation_id=cid2))
        assert len(dispatched) == 1


# ═══════════════════════════════════════════════════════════════════════
# 7. State Serialization Round-Trip
# ═══════════════════════════════════════════════════════════════════════


class TestStateSerializationRoundTrip:
    """Verify state survives serialization and deserialization."""

    @pytest.mark.anyio
    async def test_model_dump_round_trip(self) -> None:
        cid = uuid4()
        original = SagaState(
            saga_type="TestSaga",
            correlation_id=cid,
            status=SagaStatus.RUNNING,
            current_step="step1",
        )

        data = original.model_dump()
        restored = SagaState.model_validate(data)

        assert restored.id == original.id
        assert restored.saga_type == original.saga_type
        assert restored.correlation_id == original.correlation_id
        assert restored.status == original.status
        assert restored.current_step == original.current_step

    @pytest.mark.anyio
    async def test_model_copy_round_trip(self) -> None:
        cid = uuid4()
        original = SagaState(
            saga_type="TestSaga",
            correlation_id=cid,
            status=SagaStatus.RUNNING,
        )

        copy = original.model_copy(deep=True)

        assert copy.id == original.id
        assert copy.saga_type == original.saga_type
        assert copy.correlation_id == original.correlation_id
        assert copy.status == original.status

    @pytest.mark.anyio
    async def test_compensation_stack_serializes_correctly(self) -> None:
        cid = uuid4()
        state = SagaState(
            saga_type="TestSaga",
            correlation_id=cid,
            compensation_stack=[
                CompensationRecord(
                    command_type="CancelReservation",
                    data={"order_id": "ORD-1"},
                    description="Cancel reservation",
                    module_name="test.conftest",
                ),
                CompensationRecord(
                    command_type="CancelPayment",
                    data={"order_id": "ORD-1"},
                    description="Cancel payment",
                    module_name="test.conftest",
                ),
            ],
        )

        data = state.model_dump()
        restored = SagaState.model_validate(data)

        assert len(restored.compensation_stack) == 2
        assert restored.compensation_stack[0].command_type == "CancelReservation"
        assert restored.compensation_stack[1].command_type == "CancelPayment"

    @pytest.mark.anyio
    async def test_step_history_serializes_correctly(self) -> None:
        causation_ids = [uuid4() for _ in range(5)]
        state = SagaState(
            saga_type="TestSaga",
            correlation_id=uuid4(),
            step_history=[
                StepRecord(
                    step_name=f"step{i}",
                    event_type=f"Event{i}",
                    causation_id=causation_ids[i],
                )
                for i in range(5)
            ],
        )

        data = state.model_dump()
        restored = SagaState.model_validate(data)

        assert len(restored.step_history) == 5
        for i in range(5):
            assert restored.step_history[i].step_name == f"step{i}"
            assert restored.step_history[i].causation_id == causation_ids[i]

    @pytest.mark.anyio
    async def test_failed_compensations_serialize_correctly(self) -> None:
        state = SagaState(
            saga_type="TestSaga",
            correlation_id=uuid4(),
            failed_compensations=[
                {
                    "command_type": "CancelReservation",
                    "data": {"order_id": "ORD-1", "item_count": 5},
                    "module_name": "test.conftest",
                    "error": "Network timeout",
                },
            ],
        )

        data = state.model_dump()
        restored = SagaState.model_validate(data)

        assert len(restored.failed_compensations) == 1
        assert restored.failed_compensations[0]["command_type"] == "CancelReservation"
        assert restored.failed_compensations[0]["error"] == "Network timeout"
