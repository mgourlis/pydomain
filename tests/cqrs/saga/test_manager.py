"""Tests for SagaManager — lifecycle orchestration."""

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

# ── Test domain events ──────────────────────────────────────────────────


class OrderCreated(DomainEvent):
    order_id: str


class ItemsReserved(DomainEvent):
    order_id: str


class OrderShipped(DomainEvent):
    order_id: str


class PaymentProcessed(DomainEvent):
    order_id: str


class DeliveryScheduled(DomainEvent):
    order_id: str


class OrderConfirmed(DomainEvent):
    order_id: str


# ── Test commands ───────────────────────────────────────────────────────


class ReserveItems(Command[EmptyCommandResult]):
    order_id: str


class ShipOrder(Command[EmptyCommandResult]):
    order_id: str


class ProcessPayment(Command[EmptyCommandResult]):
    order_id: str


class ScheduleDelivery(Command[EmptyCommandResult]):
    order_id: str


class ConfirmOrder(Command[EmptyCommandResult]):
    order_id: str


class CancelReservation(Command[EmptyCommandResult]):
    order_id: str


class CancelPayment(Command[EmptyCommandResult]):
    order_id: str


class CancelShipping(Command[EmptyCommandResult]):
    order_id: str


# ── Test saga ───────────────────────────────────────────────────────────


class OrderSaga(Saga[SagaState]):
    listens_to = [OrderCreated, ItemsReserved]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            step="reserving",
        )
        self.on(
            ItemsReserved,
            send=lambda e: ShipOrder(order_id=e.order_id),
            step="shipping",
            complete=True,
        )


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def saga_repo() -> FakeSagaRepository:
    return FakeSagaRepository()


@pytest.fixture
def saga_registry() -> SagaRegistry:
    registry = SagaRegistry()
    registry.register_saga(OrderSaga)
    return registry


@pytest.fixture
def command_bus() -> CommandBus:
    bus = CommandBus()

    async def handle_reserve(cmd: ReserveItems, uow: Any = None) -> EmptyCommandResult:
        return EmptyCommandResult()

    async def handle_ship(cmd: ShipOrder, uow: Any = None) -> EmptyCommandResult:
        return EmptyCommandResult()

    bus.register(ReserveItems, handle_reserve, uow_factory=lambda: FakeUnitOfWork())
    bus.register(ShipOrder, handle_ship, uow_factory=lambda: FakeUnitOfWork())
    return bus


@pytest.fixture
def manager(
    saga_repo: FakeSagaRepository,
    saga_registry: SagaRegistry,
    command_bus: CommandBus,
) -> SagaManager:
    return SagaManager(
        repository=saga_repo,
        registry=saga_registry,
        command_bus=command_bus,
    )

    """handle() — route event to registered sagas."""

    @pytest.mark.anyio
    async def test_handle_creates_saga_on_first_event(
        self,
        manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await manager.handle(event)

        state = await saga_repo.find_by_correlation_id(cid, "OrderSaga")
        assert state is not None
        assert state.saga_type == "OrderSaga"
        assert state.status == SagaStatus.RUNNING

    @pytest.mark.anyio
    async def test_handle_persisted_state_has_step_history(
        self,
        manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await manager.handle(event)

        state = await saga_repo.find_by_correlation_id(cid, "OrderSaga")
        assert state is not None
        assert len(state.step_history) == 1

    @pytest.mark.anyio
    async def test_handle_persists_pending_commands(
        self,
        manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await manager.handle(event)

        state = await saga_repo.find_by_correlation_id(cid, "OrderSaga")
        assert state is not None
        # After dispatch, pending_commands should be cleared
        assert state.pending_commands == []

    @pytest.mark.anyio
    async def test_handle_ignores_events_without_correlation_id(
        self,
        manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=None)
        await manager.handle(event)
        # No saga should have been created
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
            command_bus=command_bus,
        )
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await mgr.handle(event)
        assert len(saga_repo._store) == 0

    @pytest.mark.anyio
    async def test_handle_continues_existing_saga(
        self,
        manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        cid = uuid4()
        event1 = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await manager.handle(event1)

        state_before = await saga_repo.find_by_correlation_id(cid, "OrderSaga")
        assert state_before is not None
        assert state_before.status == SagaStatus.RUNNING

        event2 = ItemsReserved(order_id="ORD-1", correlation_id=cid)
        await manager.handle(event2)

        state_after = await saga_repo.find_by_correlation_id(cid, "OrderSaga")
        assert state_after is not None
        assert state_after.status == SagaStatus.COMPLETED
        assert len(state_after.step_history) == 2


class TestManagerStartSaga:
    """start_saga() — explicit orchestration entry point."""

    @pytest.mark.anyio
    async def test_start_saga_returns_saga_id(
        self,
        manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        saga_id = await manager.start_saga(OrderSaga, event)
        assert saga_id is not None
        assert await saga_repo.get_by_id(saga_id) is not None

    @pytest.mark.anyio
    async def test_start_saga_uses_provided_correlation_id(
        self,
        manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        explicit_cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        saga_id = await manager.start_saga(
            OrderSaga, event, correlation_id=explicit_cid
        )
        assert saga_id is not None
        state = await saga_repo.get_by_id(saga_id)
        assert state is not None
        assert state.correlation_id == explicit_cid

    @pytest.mark.anyio
    async def test_start_saga_generates_correlation_id_when_none(
        self,
        manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        event = OrderCreated(order_id="ORD-1")  # no correlation_id
        saga_id = await manager.start_saga(OrderSaga, event)
        assert saga_id is not None
        state = await saga_repo.get_by_id(saga_id)
        assert state is not None
        assert state.correlation_id is not None


class TestManagerTerminalState:
    """Terminal state handling — saga is skipped."""

    @pytest.mark.anyio
    async def test_terminal_saga_is_skipped(
        self,
        manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        cid = uuid4()
        event1 = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await manager.handle(event1)

        event2 = ItemsReserved(order_id="ORD-1", correlation_id=cid)
        await manager.handle(event2)

        state = await saga_repo.find_by_correlation_id(cid, "OrderSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPLETED

        # Send another event — saga should be skipped
        event3 = OrderCreated(order_id="ORD-2", correlation_id=cid)
        await manager.handle(event3)

        state_after = await saga_repo.find_by_correlation_id(cid, "OrderSaga")
        assert state_after is not None
        # Step history should still be 2 (not 3)
        assert len(state_after.step_history) == 2


# ── 5-step saga for compensation scenarios ──────────────────────────────


class FiveStepSaga(Saga[SagaState]):
    """5-step saga where steps 1–3 have compensations, steps 4–5 do not.

    Uses custom handlers so we can control exactly when failures
    occur relative to compensation registration.

    Step 1: OrderCreated   → ReserveItems     (compensate: CancelReservation)
    Step 2: ItemsReserved  → ProcessPayment   (compensate: CancelPayment)
    Step 3: PaymentProcessed → ShipOrder      (compensate: CancelShipping)
    Step 4: OrderShipped   → ScheduleDelivery  (no compensation)
    Step 5: DeliveryScheduled → ConfirmOrder   (no compensation, complete)
    """

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
        self.state.current_step = "step1_reserve"
        self.dispatch(ReserveItems(order_id="ORD-1"))
        self.add_compensation(
            CancelReservation(order_id="ORD-1"),
            description="Cancel reservation",
        )

    async def _step2(self, event: DomainEvent) -> None:
        self.state.current_step = "step2_payment"
        self.dispatch(ProcessPayment(order_id="ORD-1"))
        self.add_compensation(
            CancelPayment(order_id="ORD-1"),
            description="Cancel payment",
        )

    async def _step3(self, event: DomainEvent) -> None:
        self.state.current_step = "step3_ship"
        self.dispatch(ShipOrder(order_id="ORD-1"))
        self.add_compensation(
            CancelShipping(order_id="ORD-1"),
            description="Cancel shipping",
        )

    async def _step4(self, event: DomainEvent) -> None:
        self.state.current_step = "step4_delivery"
        self.dispatch(ScheduleDelivery(order_id="ORD-1"))

    async def _step5(self, event: DomainEvent) -> None:
        self.state.current_step = "step5_confirm"
        self.dispatch(ConfirmOrder(order_id="ORD-1"))
        self.complete()


class FiveStepNoCompensationSaga(Saga[SagaState]):
    """5-step saga with NO compensations on any step.

    Step 1: OrderCreated   → ReserveItems
    Step 2: ItemsReserved  → ProcessPayment
    Step 3: PaymentProcessed → ShipOrder
    Step 4: OrderShipped   → ScheduleDelivery
    Step 5: DeliveryScheduled → ConfirmOrder (complete)
    """

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
        self.state.current_step = "step1_reserve"
        self.dispatch(ReserveItems(order_id="ORD-1"))

    async def _step2(self, event: DomainEvent) -> None:
        self.state.current_step = "step2_payment"
        self.dispatch(ProcessPayment(order_id="ORD-1"))

    async def _step3(self, event: DomainEvent) -> None:
        self.state.current_step = "step3_ship"
        self.dispatch(ShipOrder(order_id="ORD-1"))

    async def _step4(self, event: DomainEvent) -> None:
        self.state.current_step = "step4_delivery"
        self.dispatch(ScheduleDelivery(order_id="ORD-1"))

    async def _step5(self, event: DomainEvent) -> None:
        self.state.current_step = "step5_confirm"
        self.dispatch(ConfirmOrder(order_id="ORD-1"))
        self.complete()


# ── Fixtures for compensation scenario tests ────────────────────────────


@pytest.fixture
def five_step_registry() -> SagaRegistry:
    registry = SagaRegistry()
    registry.register_saga(FiveStepSaga)
    return registry


@pytest.fixture
def five_step_no_comp_registry() -> SagaRegistry:
    registry = SagaRegistry()
    registry.register_saga(FiveStepNoCompensationSaga)
    return registry


@pytest.fixture
def full_command_bus() -> CommandBus:
    """Command bus with handlers for all test commands."""
    bus = CommandBus()

    async def noop(cmd: Any, uow: Any = None) -> EmptyCommandResult:
        return EmptyCommandResult()

    for cmd_type in (
        ReserveItems,
        ShipOrder,
        ProcessPayment,
        ScheduleDelivery,
        ConfirmOrder,
        CancelReservation,
        CancelPayment,
        CancelShipping,
    ):
        bus.register(cmd_type, noop, uow_factory=lambda: FakeUnitOfWork())

    return bus


@pytest.fixture
def five_step_manager(
    saga_repo: FakeSagaRepository,
    five_step_registry: SagaRegistry,
    full_command_bus: CommandBus,
) -> SagaManager:
    return SagaManager(
        repository=saga_repo,
        registry=five_step_registry,
        command_bus=full_command_bus,
    )


@pytest.fixture
def no_comp_manager(
    saga_repo: FakeSagaRepository,
    five_step_no_comp_registry: SagaRegistry,
    full_command_bus: CommandBus,
) -> SagaManager:
    return SagaManager(
        repository=saga_repo,
        registry=five_step_no_comp_registry,
        command_bus=full_command_bus,
    )


class TestCompensationScenarios:
    """End-to-end compensation scenarios through the SagaManager.

    Scenario 1: Saga with 5 steps (3 with compensations), fails at step 2.
                Only step 1's compensation runs (step 2 failed before
                its compensation was registered).

    Scenario 2: Saga with 5 steps (3 with compensations), fails at step 5.
                All 3 compensations run successfully → COMPENSATED.
                If any compensation fails → FAILED.

    Scenario 3: Saga with 5 steps (no compensations), fails at any step.
                Marked as FAILED (nothing to compensate).
    """

    @pytest.mark.anyio
    async def test_fail_at_step2_compensates_only_step1(
        self,
        five_step_manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """Fail at step 2 — only step 1's compensation runs.

        Step 1 completes (adds CancelReservation compensation).
        Step 2 handler raises before adding its compensation.
        Result: only CancelReservation is dispatched, status = COMPENSATED.
        """
        cid = uuid4()

        # Step 1: succeeds, adds CancelReservation to compensation stack
        event1 = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await five_step_manager.handle(event1)

        state = await saga_repo.find_by_correlation_id(cid, "FiveStepSaga")
        assert state is not None
        assert state.status == SagaStatus.RUNNING
        assert len(state.compensation_stack) == 1
        assert state.compensation_stack[0].command_type == "CancelReservation"

        # Step 2: We need the handler to raise BEFORE add_compensation.
        # The FiveStepSaga._step2 adds compensation at the end, so to
        # simulate "fail before compensation" we need a custom saga.
        # Instead, let's test with a saga where step 2 fails mid-handler.
        # We'll use a modified approach: inject a failing handler.
        cid2 = uuid4()

        class Step2FailSaga(Saga[SagaState]):
            """Saga where step 2 raises before adding compensation."""

            listens_to = [OrderCreated, ItemsReserved]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)
                self.on(ItemsReserved, handler=self._step2_fail)

            async def _step1(self, event: DomainEvent) -> None:
                self.state.current_step = "step1"
                self.dispatch(ReserveItems(order_id="ORD-1"))
                self.add_compensation(
                    CancelReservation(order_id="ORD-1"),
                    description="Cancel reservation",
                )

            async def _step2_fail(self, event: DomainEvent) -> None:
                self.state.current_step = "step2"
                self.dispatch(ProcessPayment(order_id="ORD-1"))
                # FAIL HERE — before add_compensation
                raise RuntimeError("Payment gateway unreachable")

        registry = SagaRegistry()
        registry.register_saga(Step2FailSaga)
        mgr = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=five_step_manager.command_bus,
        )

        event1 = OrderCreated(order_id="ORD-1", correlation_id=cid2)
        await mgr.handle(event1)

        state = await saga_repo.find_by_correlation_id(cid2, "Step2FailSaga")
        assert state is not None
        assert len(state.compensation_stack) == 1

        # Step 2: handler raises before compensation is registered
        event2 = ItemsReserved(order_id="ORD-1", correlation_id=cid2)
        await mgr.handle(event2)

        state = await saga_repo.find_by_correlation_id(cid2, "Step2FailSaga")
        assert state is not None
        # Step 1's compensation was dispatched; step 2's was never registered
        assert state.status == SagaStatus.COMPENSATED
        assert len(state.failed_compensations) == 0
        # Only step 1's compensation was on the stack
        assert state.compensation_stack == []
        assert "Payment gateway unreachable" in (state.error or "")

    @pytest.mark.anyio
    async def test_fail_at_step5_all_compensations_succeed_is_compensated(
        self,
        five_step_manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """Fail at step 5 — all 3 compensations succeed → COMPENSATED."""
        cid = uuid4()

        # We need a saga where step 5 fails. FiveStepSaga completes at step 5,
        # so we use a variant that fails instead.
        class Step5FailSaga(Saga[SagaState]):
            """5-step saga that fails at step 5 (after 3 compensations added)."""

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
                    CancelReservation(order_id="ORD-1"),
                    description="Cancel reservation",
                )

            async def _step2(self, event: DomainEvent) -> None:
                self.state.current_step = "step2"
                self.dispatch(ProcessPayment(order_id="ORD-1"))
                self.add_compensation(
                    CancelPayment(order_id="ORD-1"),
                    description="Cancel payment",
                )

            async def _step3(self, event: DomainEvent) -> None:
                self.state.current_step = "step3"
                self.dispatch(ShipOrder(order_id="ORD-1"))
                self.add_compensation(
                    CancelShipping(order_id="ORD-1"),
                    description="Cancel shipping",
                )

            async def _step4(self, event: DomainEvent) -> None:
                self.state.current_step = "step4"
                self.dispatch(ScheduleDelivery(order_id="ORD-1"))

            async def _step5_fail(self, event: DomainEvent) -> None:
                self.state.current_step = "step5"
                self.dispatch(ConfirmOrder(order_id="ORD-1"))
                # Fail after step 5 runs — all 3 compensations already registered
                raise RuntimeError("Confirmation service unavailable")

        registry = SagaRegistry()
        registry.register_saga(Step5FailSaga)
        mgr = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=five_step_manager.command_bus,
        )

        # Process steps 1–4 successfully
        for event_cls in (
            OrderCreated,
            ItemsReserved,
            PaymentProcessed,
            OrderShipped,
        ):
            evt = event_cls(order_id="ORD-1", correlation_id=cid)  # type: ignore[call-arg]
            await mgr.handle(evt)

        state = await saga_repo.find_by_correlation_id(cid, "Step5FailSaga")
        assert state is not None
        assert len(state.compensation_stack) == 3  # steps 1, 2, 3

        # Step 5: fails — all 3 compensations should run
        event5 = DeliveryScheduled(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event5)

        state = await saga_repo.find_by_correlation_id(cid, "Step5FailSaga")
        assert state is not None
        # All compensations succeeded → COMPENSATED
        assert state.status == SagaStatus.COMPENSATED
        assert len(state.failed_compensations) == 0
        assert state.compensation_stack == []
        assert "Confirmation service unavailable" in (state.error or "")

    @pytest.mark.anyio
    async def test_fail_at_step5_one_compensation_fails_is_failed(
        self,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """Fail at step 5 — one compensation fails → FAILED."""

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
                    CancelReservation(order_id="ORD-1"),
                    description="Cancel reservation",
                )

            async def _step2(self, event: DomainEvent) -> None:
                self.state.current_step = "step2"
                self.dispatch(ProcessPayment(order_id="ORD-1"))
                self.add_compensation(
                    CancelPayment(order_id="ORD-1"),
                    description="Cancel payment",
                )

            async def _step3(self, event: DomainEvent) -> None:
                self.state.current_step = "step3"
                self.dispatch(ShipOrder(order_id="ORD-1"))
                self.add_compensation(
                    CancelShipping(order_id="ORD-1"),
                    description="Cancel shipping",
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

        # Command bus where CancelShipping always fails
        bus = CommandBus()

        async def noop(cmd: Any, uow: Any = None) -> EmptyCommandResult:
            return EmptyCommandResult()

        async def fail_shipping(cmd: Any, uow: Any = None) -> EmptyCommandResult:
            raise RuntimeError("Shipping service down")

        for cmd_type in (
            ReserveItems,
            ProcessPayment,
            ConfirmOrder,
            ScheduleDelivery,
            ShipOrder,
            CancelReservation,
            CancelPayment,
        ):
            bus.register(cmd_type, noop, uow_factory=lambda: FakeUnitOfWork())
        bus.register(
            CancelShipping, fail_shipping, uow_factory=lambda: FakeUnitOfWork()
        )

        mgr = SagaManager(repository=saga_repo, registry=registry, command_bus=bus)

        cid = uuid4()
        for event_cls in (
            OrderCreated,
            ItemsReserved,
            PaymentProcessed,
            OrderShipped,
        ):
            evt = event_cls(order_id="ORD-1", correlation_id=cid)  # type: ignore[call-arg]
            await mgr.handle(evt)

        event5 = DeliveryScheduled(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event5)

        state = await saga_repo.find_by_correlation_id(cid, "Step5FailSaga")
        assert state is not None
        # One compensation failed → FAILED
        assert state.status == SagaStatus.FAILED
        assert len(state.failed_compensations) == 1
        assert state.failed_compensations[0]["command_type"] == "CancelShipping"

    @pytest.mark.anyio
    async def test_no_compensations_fail_at_step3_is_failed(
        self,
        no_comp_manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """No compensations — fail at any step → FAILED."""

        class Step3FailSaga(Saga[SagaState]):
            """5-step saga, no compensations, fails at step 3."""

            listens_to = [OrderCreated, ItemsReserved, PaymentProcessed]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(OrderCreated, handler=self._step1)
                self.on(ItemsReserved, handler=self._step2)
                self.on(PaymentProcessed, handler=self._step3_fail)

            async def _step1(self, event: DomainEvent) -> None:
                self.state.current_step = "step1"
                self.dispatch(ReserveItems(order_id="ORD-1"))

            async def _step2(self, event: DomainEvent) -> None:
                self.state.current_step = "step2"
                self.dispatch(ProcessPayment(order_id="ORD-1"))

            async def _step3_fail(self, event: DomainEvent) -> None:
                self.state.current_step = "step3"
                raise RuntimeError("Shipping service unavailable")

        registry = SagaRegistry()
        registry.register_saga(Step3FailSaga)
        mgr = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=no_comp_manager.command_bus,
        )

        cid = uuid4()
        event1 = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event1)

        event2 = ItemsReserved(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event2)

        # Step 3: fails, no compensations → FAILED
        event3 = PaymentProcessed(order_id="ORD-1", correlation_id=cid)
        await mgr.handle(event3)

        state = await saga_repo.find_by_correlation_id(cid, "Step3FailSaga")
        assert state is not None
        assert state.status == SagaStatus.FAILED
        assert state.error == "Shipping service unavailable"
        assert len(state.compensation_stack) == 0


# ── Recovery tests ──────────────────────────────────────────────────────


class TestRecoverPendingSagas:
    """recover_pending_sagas() — re-dispatch stalled saga commands."""

    @pytest.mark.anyio
    async def test_recover_pending_sagas_redispatches_commands(
        self,
        manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """Stalled saga with undispatched commands → recovery re-dispatches."""
        cid = uuid4()
        state = SagaState(
            saga_type="OrderSaga",
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

        await manager.recover_pending_sagas()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        # Commands were cleared after successful dispatch
        assert updated.pending_commands == []
        # retry_count reset to 0
        assert updated.retry_count == 0

    @pytest.mark.anyio
    async def test_recover_pending_sagas_fails_on_max_retries(
        self,
        saga_repo: FakeSagaRepository,
        saga_registry: SagaRegistry,
        command_bus: CommandBus,
    ) -> None:
        """Stalled saga at max retries → FAILED."""
        manager = SagaManager(
            repository=saga_repo,
            registry=saga_registry,
            command_bus=command_bus,
        )
        state = SagaState(
            saga_type="OrderSaga",
            status=SagaStatus.RUNNING,
            retry_count=3,
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
        manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """Successful recovery resets retry_count to 0."""
        state = SagaState(
            saga_type="OrderSaga",
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

        await manager.recover_pending_sagas()

        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.retry_count == 0
        assert updated.pending_commands == []

    @pytest.mark.anyio
    async def test_recover_pending_sagas_skips_unknown_saga_type(
        self,
        manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """Unknown saga_type → silently skipped, no crash."""
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

        # Should not raise
        await manager.recover_pending_sagas()

        # State unchanged
        updated = await saga_repo.get_by_id(state.id)
        assert updated is not None
        assert updated.pending_commands  # still has undispatched commands


class TestProcessTimeouts:
    """process_timeouts() — handle expired suspended sagas."""

    @pytest.mark.anyio
    async def test_process_timeouts_calls_on_timeout(
        self,
        manager: SagaManager,
        saga_repo: FakeSagaRepository,
    ) -> None:
        """Expired timeout → on_timeout (default: fail) → FAILED via COMPENSATING."""
        from datetime import timedelta

        cid = uuid4()
        # Create a saga with a past timeout
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await manager.handle(event)

        # Manually suspend with expired timeout
        saga_state = await saga_repo.find_by_correlation_id(cid, "OrderSaga")
        assert saga_state is not None
        saga_state.status = SagaStatus.SUSPENDED
        saga_state.suspension_reason = "waiting for payment"
        saga_state.suspended_at = datetime.now(UTC)
        saga_state.timeout_at = datetime.now(UTC) - timedelta(hours=1)
        await saga_repo.save(saga_state)

        await manager.process_timeouts()

        updated = await saga_repo.get_by_id(saga_state.id)
        assert updated is not None
        # Default on_timeout calls fail() which triggers compensation
        assert updated.status in (SagaStatus.COMPENSATING, SagaStatus.FAILED)
        assert "timed out" in (updated.error or "").lower()

    @pytest.mark.anyio
    async def test_process_timeouts_custom_recovery(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """Override on_timeout to resume → RUNNING + forward commands."""
        from datetime import timedelta

        dispatched_commands: list[Command[Any]] = []

        async def capture_reserve(
            cmd: ReserveItems, uow: Any = None
        ) -> EmptyCommandResult:
            dispatched_commands.append(cmd)
            return EmptyCommandResult()

        bus = CommandBus()
        bus.register(
            ReserveItems, capture_reserve, uow_factory=lambda: FakeUnitOfWork()
        )

        class TimeoutRetrySaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: ReserveItems(order_id=e.order_id),
                    step="reserving",
                )

            async def on_timeout(self) -> None:
                self.resume()
                self.dispatch(ReserveItems(order_id="ORD-RETRY"))

        registry = SagaRegistry()
        registry.register_saga(TimeoutRetrySaga)
        manager = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=bus,
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
    async def test_process_timeouts_on_timeout_raises(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """on_timeout raises → force-fail the saga."""

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
            command_bus=command_bus,
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


class TestBindTo:
    """bind_to() — auto-register manager as event handler."""

    def test_bind_to_registers_all_event_types(self) -> None:
        """bind_to() registers the manager as handler for all saga events."""
        registry = SagaRegistry()
        registry.register_saga(OrderSaga)
        saga_repo = FakeSagaRepository()
        command_bus = CommandBus()
        manager = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=command_bus,
        )

        registered: dict[type, list] = {}

        class FakeDispatcher:
            def register_event(
                self, event_type: type, handler: object, **kwargs: object
            ) -> None:
                registered.setdefault(event_type, []).append(handler)

        manager.bind_to(FakeDispatcher())

        # OrderSaga listens_to = [OrderCreated, ItemsReserved]
        assert OrderCreated in registered
        assert ItemsReserved in registered
        assert registered[OrderCreated] == [manager.handle]
        assert registered[ItemsReserved] == [manager.handle]

    def test_bind_to_empty_registry_is_noop(self) -> None:
        """bind_to() with no registered events does nothing."""
        registry = SagaRegistry()
        saga_repo = FakeSagaRepository()
        command_bus = CommandBus()
        manager = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=command_bus,
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
        """bind_to() registers handle once per event type, not per saga."""
        registry = SagaRegistry()
        registry.register_saga(OrderSaga)

        # Register a second saga that also listens to OrderCreated
        class AnotherSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

        registry.register_saga(AnotherSaga)

        saga_repo = FakeSagaRepository()
        command_bus = CommandBus()
        manager = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=command_bus,
        )

        registered: dict[type, list] = {}

        class FakeDispatcher:
            def register_event(
                self, event_type: type, handler: object, **kwargs: object
            ) -> None:
                registered.setdefault(event_type, []).append(handler)

        manager.bind_to(FakeDispatcher())

        # OrderCreated appears once even though two sagas listen to it
        assert registered[OrderCreated] == [manager.handle]

    @pytest.mark.anyio
    async def test_process_timeouts_dispatches_compensations(
        self,
        saga_repo: FakeSagaRepository,
        command_bus: CommandBus,
    ) -> None:
        """on_timeout triggers compensation → COMPENSATING → COMPENSATED."""
        from datetime import timedelta

        compensated_commands: list[Command[Any]] = []

        async def capture_cancel(
            cmd: CancelReservation, uow: Any = None
        ) -> EmptyCommandResult:
            compensated_commands.append(cmd)
            return EmptyCommandResult()

        async def noop_reserve(
            cmd: ReserveItems, uow: Any = None
        ) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus = CommandBus()
        bus.register(
            CancelReservation, capture_cancel, uow_factory=lambda: FakeUnitOfWork()
        )
        bus.register(ReserveItems, noop_reserve, uow_factory=lambda: FakeUnitOfWork())

        class CompensatingTimeoutSaga(Saga[SagaState]):
            listens_to = [OrderCreated]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: ReserveItems(order_id=e.order_id),
                    step="reserving",
                    compensate=lambda e: CancelReservation(order_id=e.order_id),
                    compensate_description="Cancel reservation",
                )

        registry = SagaRegistry()
        registry.register_saga(CompensatingTimeoutSaga)
        manager = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=bus,
        )

        # Create a saga in RUNNING with step history, then suspend with expired timeout
        cid = uuid4()
        event = OrderCreated(order_id="ORD-1", correlation_id=cid)
        await manager.handle(event)

        saga_state = await saga_repo.find_by_correlation_id(
            cid, "CompensatingTimeoutSaga"
        )
        assert saga_state is not None
        # Step was registered, so compensation stack should have an entry
        assert len(saga_state.compensation_stack) > 0

        saga_state.status = SagaStatus.SUSPENDED
        saga_state.suspension_reason = "waiting"
        saga_state.timeout_at = datetime.now(UTC) - timedelta(hours=1)
        await saga_repo.save(saga_state)

        await manager.process_timeouts()

        updated = await saga_repo.get_by_id(saga_state.id)
        assert updated is not None
        # Default on_timeout calls fail(compensate=True) → COMPENSATING → COMPENSATED
        assert updated.status == SagaStatus.COMPENSATED
        assert len(compensated_commands) == 1
