"""Shared fixtures and domain types for saga tests."""

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
)
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing import FakeUnitOfWork
from pydomain.testing.fake_saga_repository import FakeSagaRepository

# ═══════════════════════════════════════════════════════════════════════
# Domain Events
# ═══════════════════════════════════════════════════════════════════════


class OrderCreated(DomainEvent):
    order_id: str


class ItemsReserved(DomainEvent):
    order_id: str
    item_count: int = 0


class PaymentProcessed(DomainEvent):
    order_id: str
    amount: float = 0.0


class OrderShipped(DomainEvent):
    order_id: str
    tracking_number: str = ""


class DeliveryScheduled(DomainEvent):
    order_id: str
    delivery_date: str = ""


class OrderConfirmed(DomainEvent):
    order_id: str


class OrderFailed(DomainEvent):
    order_id: str
    reason: str


class ApprovalRequested(DomainEvent):
    order_id: str
    approver: str = "manager"


class ApprovalGranted(DomainEvent):
    order_id: str
    approved_by: str = ""


class PaymentDeclined(DomainEvent):
    order_id: str
    reason: str = ""


class TransactionFlaggedForFraud(DomainEvent):
    customer_id: str
    risk_score: int = 0


class FraudReviewApproved(DomainEvent):
    order_id: str
    agent_role: str = "JUNIOR_AGENT"


class FraudReviewRejected(DomainEvent):
    order_id: str
    agent_id: str = ""


# ═══════════════════════════════════════════════════════════════════════
# Commands
# ═══════════════════════════════════════════════════════════════════════


class ReserveItems(Command[EmptyCommandResult]):
    order_id: str
    item_count: int = 1


class ProcessPayment(Command[EmptyCommandResult]):
    order_id: str
    amount: float = 0.0


class ShipOrder(Command[EmptyCommandResult]):
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


class CancelOrder(Command[EmptyCommandResult]):
    order_id: str


class SendNotification(Command[EmptyCommandResult]):
    order_id: str
    message: str = ""


class RequestApproval(Command[EmptyCommandResult]):
    order_id: str
    approver: str = "manager"


class LogFraudFlag(Command[EmptyCommandResult]):
    customer_id: str
    risk_score: int = 0


class NotifyCustomerOfCancellation(Command[EmptyCommandResult]):
    customer_id: str


# ═══════════════════════════════════════════════════════════════════════
# Saga Definitions
# ═══════════════════════════════════════════════════════════════════════


class TwoStepSaga(Saga[SagaState]):
    """Simple 2-step saga:
    OrderCreated → ReserveItems → ItemsReserved → ConfirmOrder."""

    listens_to = [OrderCreated, ItemsReserved]

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


class FiveStepSaga(Saga[SagaState]):
    """5-step saga: reserve → pay → ship → schedule → confirm.

    Steps 1–3 have compensations; steps 4–5 do not.
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
        self.add_compensation(CancelReservation(order_id="ORD-1"), "Cancel reservation")

    async def _step2(self, event: DomainEvent) -> None:
        self.state.current_step = "step2_payment"
        self.dispatch(ProcessPayment(order_id="ORD-1"))
        self.add_compensation(CancelPayment(order_id="ORD-1"), "Cancel payment")

    async def _step3(self, event: DomainEvent) -> None:
        self.state.current_step = "step3_ship"
        self.dispatch(ShipOrder(order_id="ORD-1"))
        self.add_compensation(CancelShipping(order_id="ORD-1"), "Cancel shipping")

    async def _step4(self, event: DomainEvent) -> None:
        self.state.current_step = "step4_delivery"
        self.dispatch(ScheduleDelivery(order_id="ORD-1"))

    async def _step5(self, event: DomainEvent) -> None:
        self.state.current_step = "step5_confirm"
        self.dispatch(ConfirmOrder(order_id="ORD-1"))
        self.complete()


class SuspendableSaga(Saga[SagaState]):
    """Saga that suspends for human approval."""

    listens_to = [OrderCreated, ApprovalGranted]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: RequestApproval(order_id=e.order_id),
            step="awaiting_approval",
            suspend=True,
            suspend_reason="Waiting for manager approval",
            suspend_timeout=timedelta(hours=24),
        )
        self.on(
            ApprovalGranted,
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


class OverrideHandleEventSaga(Saga[SagaState]):
    """Saga overriding _handle_event() directly with match/case."""

    listens_to = [OrderCreated, ItemsReserved, OrderFailed]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        # No on() calls — uses _handle_event override

    async def _handle_event(self, event: DomainEvent) -> None:
        match event:
            case OrderCreated():
                self.dispatch(ReserveItems(order_id=event.order_id, item_count=5))
                self.add_compensation(
                    CancelReservation(order_id=event.order_id),
                    "Cancel reservation",
                )
            case ItemsReserved():
                self.dispatch(ConfirmOrder(order_id=event.order_id))
                self.complete()
            case OrderFailed():
                await self.fail(event.reason)
            case _:
                pass  # Silently ignore unknown events


class NoListenSaga(Saga[SagaState]):
    """Saga with no listens_to — for name-only registration tests."""

    listens_to = []


class MultiDispatchSaga(Saga[SagaState]):
    """Saga that dispatches multiple commands from a single event."""

    listens_to = [OrderCreated]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(OrderCreated, handler=self._on_order_created)

    async def _on_order_created(self, event: DomainEvent) -> None:
        self.dispatch(ReserveItems(order_id=event.order_id, item_count=5))
        self.dispatch(SendNotification(order_id=event.order_id, message="Processing"))
        self.add_compensation(
            CancelReservation(order_id=event.order_id), "Cancel reservation"
        )


class TimeoutRetrySaga(Saga[SagaState]):
    """Saga that overrides on_timeout to retry instead of failing."""

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
        self.dispatch(ReserveItems(order_id="ORD-RETRY", item_count=1))


class AuditSaga(Saga[SagaState]):
    """Second saga listening to OrderCreated — for multi-saga tests."""

    listens_to = [OrderCreated]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: SendNotification(
                order_id=e.order_id, message="Order created"
            ),
            step="auditing",
            complete=True,
        )


class FailSaga(Saga[SagaState]):
    """Saga that uses fail=True declarative failure with a callable reason."""

    listens_to = [OrderCreated, FraudReviewRejected]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            step="charging_customer",
            compensate=lambda e: CancelReservation(order_id=e.order_id),
        )
        self.on(
            FraudReviewRejected,
            send=lambda e: NotifyCustomerOfCancellation(customer_id=e.order_id),
            fail=True,
            fail_reason=lambda e: f"Agent {e.agent_id} rejected the order.",
        )


class FailSagaStaticReason(Saga[SagaState]):
    """Saga that uses fail=True with a static fail_reason string."""

    listens_to = [OrderCreated]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            fail=True,
            fail_reason="Order permanently rejected",
        )


class CallableReasonsSaga(Saga[SagaState]):
    """Saga using callables for all reason/description parameters."""

    listens_to = [OrderCreated, TransactionFlaggedForFraud]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            step="reserving",
            compensate=lambda e: CancelReservation(order_id=e.order_id),
            compensate_description=lambda e: f"Cancel reservation for {e.order_id}",
        )
        self.on(
            TransactionFlaggedForFraud,
            send=lambda e: LogFraudFlag(
                customer_id=e.customer_id, risk_score=e.risk_score
            ),
            step="logging_fraud_flag",
            suspend=True,
            suspend_reason=lambda e: f"Awaiting review for risk score {e.risk_score}",
            suspend_timeout=timedelta(hours=24),
        )


class ResumeFromSaga(Saga[SagaState]):
    """Saga that uses resumes_from for step-based wake-up authorization."""

    listens_to = [OrderCreated, TransactionFlaggedForFraud, FraudReviewApproved]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            step="reserving",
        )
        self.on(
            TransactionFlaggedForFraud,
            send=lambda e: LogFraudFlag(
                customer_id=e.customer_id, risk_score=e.risk_score
            ),
            step="logging_fraud_flag",
            suspend=True,
            suspend_reason="Awaiting fraud review",
        )
        self.on(
            FraudReviewApproved,
            send=lambda e: ConfirmOrder(order_id=e.order_id),
            step="confirming",
            resumes_from="logging_fraud_flag",
            complete=True,
        )


class ResumeFromMultipleSaga(Saga[SagaState]):
    """Saga that allows an event to resume from multiple different steps."""

    listens_to = [
        OrderCreated,
        TransactionFlaggedForFraud,
        PaymentDeclined,
        FraudReviewApproved,
    ]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            step="reserving",
        )
        self.on(
            TransactionFlaggedForFraud,
            send=lambda e: LogFraudFlag(
                customer_id=e.customer_id, risk_score=e.risk_score
            ),
            step="fraud_check",
            suspend=True,
            suspend_reason="Fraud check",
        )
        self.on(
            PaymentDeclined,
            send=lambda e: CancelReservation(order_id=e.order_id),
            step="payment_check",
            suspend=True,
            suspend_reason="Payment issue",
        )
        self.on(
            FraudReviewApproved,
            send=lambda e: ConfirmOrder(order_id=e.order_id),
            step="confirming",
            resumes_from=["fraud_check", "payment_check"],
            complete=True,
        )


class ShouldResumePredicateSaga(Saga[SagaState]):
    """Saga that uses an inline should_resume predicate on a step."""

    listens_to = [OrderCreated, TransactionFlaggedForFraud, FraudReviewApproved]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            step="reserving",
        )
        self.on(
            TransactionFlaggedForFraud,
            send=lambda e: LogFraudFlag(
                customer_id=e.customer_id, risk_score=e.risk_score
            ),
            step="logging_fraud_flag",
            suspend=True,
            suspend_reason="Awaiting fraud review",
        )
        self.on(
            FraudReviewApproved,
            send=lambda e: ConfirmOrder(order_id=e.order_id),
            step="confirming",
            resumes_from="logging_fraud_flag",
            should_resume=lambda e: e.agent_role == "SENIOR_MANAGER",
            complete=True,
        )


class DefaultTimeoutSaga(Saga[SagaState]):
    """Saga with a global default_timeout that steps can override."""

    default_timeout = timedelta(days=7)
    listens_to = [OrderCreated, TransactionFlaggedForFraud, ApprovalGranted]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        # Uses global 7-day timeout
        self.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            step="reserving",
            suspend=True,
            suspend_reason="Waiting for items",
        )
        # Overrides with 24-hour timeout
        self.on(
            TransactionFlaggedForFraud,
            send=lambda e: LogFraudFlag(
                customer_id=e.customer_id, risk_score=e.risk_score
            ),
            step="fraud_flag",
            suspend=True,
            suspend_reason="Fraud review",
            suspend_timeout=timedelta(hours=24),
        )
        # Overrides with None (infinite)
        self.on(
            ApprovalGranted,
            send=lambda e: ConfirmOrder(order_id=e.order_id),
            step="confirming",
            suspend=True,
            suspend_reason="Final confirmation",
            suspend_timeout=None,
        )


class FailSagaWithCompensate(Saga[SagaState]):
    """Fail step that also registers its own compensation."""

    listens_to = [OrderCreated, FraudReviewRejected]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            step="charging",
            compensate=lambda e: CancelReservation(order_id=e.order_id),
        )
        self.on(
            FraudReviewRejected,
            send=lambda e: NotifyCustomerOfCancellation(customer_id=e.order_id),
            step="notifying",
            compensate=lambda e: CancelOrder(order_id=e.order_id),
            compensate_description="Cancel entire order",
            fail=True,
            fail_reason=lambda e: f"Agent {e.agent_id} rejected the order.",
        )


class ShouldResumeOverrideSaga(Saga[SagaState]):
    """Subclass that overrides should_resume() — bypasses base logic entirely."""

    listens_to = [OrderCreated, TransactionFlaggedForFraud, FraudReviewApproved]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            step="reserving",
        )
        self.on(
            TransactionFlaggedForFraud,
            send=lambda e: LogFraudFlag(
                customer_id=e.customer_id, risk_score=e.risk_score
            ),
            step="logging_fraud_flag",
            suspend=True,
            suspend_reason="Awaiting fraud review",
        )
        self.on(
            FraudReviewApproved,
            send=lambda e: ConfirmOrder(order_id=e.order_id),
            step="confirming",
            resumes_from="logging_fraud_flag",
            should_resume=lambda e: e.agent_role == "SENIOR_MANAGER",
            complete=True,
        )

    def should_resume(self, event: DomainEvent) -> bool:
        """Override that bypasses base logic — allows ALL events through."""
        return True


class OrderFulfillmentSaga(Saga[SagaState]):
    """Full order fulfillment saga from the plan document.
    Demonstrates: fail=True, resumes_from, should_resume predicate, callable reasons."""

    listens_to = [
        OrderCreated,
        TransactionFlaggedForFraud,
        FraudReviewApproved,
        FraudReviewRejected,
    ]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)

        # Step 1: Happy path — charge customer with refund compensation
        self.on(
            OrderCreated,
            send=lambda e: ProcessPayment(order_id=e.order_id),
            step="charging_customer",
            compensate=lambda e: CancelPayment(order_id=e.order_id),
            compensate_description=lambda e: f"Refund customer for order {e.order_id}",
        )

        # Step 2: Suspend for fraud review
        self.on(
            TransactionFlaggedForFraud,
            send=lambda e: LogFraudFlag(
                customer_id=e.customer_id, risk_score=e.risk_score
            ),
            step="logging_fraud_flag",
            suspend=True,
            suspend_reason=lambda e: (
                f"Awaiting manual fraud review for risk score {e.risk_score}"
            ),
            suspend_timeout=timedelta(hours=24),
        )

        # Step 3: Resume — human approves
        self.on(
            FraudReviewApproved,
            send=lambda e: ConfirmOrder(order_id=e.order_id),
            step="confirming_order",
            resumes_from="logging_fraud_flag",
            should_resume=lambda e: e.agent_role == "SENIOR_MANAGER",
            complete=True,
        )

        # Step 4: Fail — human rejects (declarative!)
        self.on(
            FraudReviewRejected,
            send=lambda e: NotifyCustomerOfCancellation(customer_id=e.order_id),
            step="notifying_rejection",
            resumes_from="logging_fraud_flag",
            fail=True,
            fail_reason=lambda e: f"Agent {e.agent_id} rejected the order.",
        )


# ═══════════════════════════════════════════════════════════════════════
# Helper: create a command bus with noop handlers for all commands
# ═══════════════════════════════════════════════════════════════════════


def _noop_command_bus() -> CommandBus:
    """Command bus with noop handlers for all test commands."""
    bus = CommandBus()

    async def noop(cmd: Any, uow: Any = None) -> EmptyCommandResult:
        return EmptyCommandResult()

    for cmd_type in (
        ReserveItems,
        ProcessPayment,
        ShipOrder,
        ScheduleDelivery,
        ConfirmOrder,
        CancelReservation,
        CancelPayment,
        CancelShipping,
        CancelOrder,
        SendNotification,
        RequestApproval,
        LogFraudFlag,
        NotifyCustomerOfCancellation,
    ):
        bus.register(cmd_type, noop, uow_factory=lambda: FakeUnitOfWork())

    return bus


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def saga_repo() -> FakeSagaRepository:
    return FakeSagaRepository()


@pytest.fixture
def saga_state() -> SagaState:
    return SagaState(saga_type="TwoStepSaga", correlation_id=uuid4())


@pytest.fixture
def command_bus() -> CommandBus:
    return _noop_command_bus()


@pytest.fixture
def two_step_registry() -> SagaRegistry:
    registry = SagaRegistry()
    registry.register_saga(TwoStepSaga)
    return registry


@pytest.fixture
def two_step_manager(
    saga_repo: FakeSagaRepository,
    two_step_registry: SagaRegistry,
    command_bus: CommandBus,
) -> SagaManager:
    return SagaManager(
        repository=saga_repo,
        registry=two_step_registry,
        command_bus=command_bus,
    )


@pytest.fixture
def five_step_registry() -> SagaRegistry:
    registry = SagaRegistry()
    registry.register_saga(FiveStepSaga)
    return registry


@pytest.fixture
def five_step_manager(
    saga_repo: FakeSagaRepository,
    five_step_registry: SagaRegistry,
    command_bus: CommandBus,
) -> SagaManager:
    return SagaManager(
        repository=saga_repo,
        registry=five_step_registry,
        command_bus=command_bus,
    )


@pytest.fixture
def multi_saga_registry() -> SagaRegistry:
    registry = SagaRegistry()
    registry.register_saga(TwoStepSaga)
    registry.register_saga(AuditSaga)
    return registry


@pytest.fixture
def multi_saga_manager(
    saga_repo: FakeSagaRepository,
    multi_saga_registry: SagaRegistry,
    command_bus: CommandBus,
) -> SagaManager:
    return SagaManager(
        repository=saga_repo,
        registry=multi_saga_registry,
        command_bus=command_bus,
    )
