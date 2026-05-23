"""Integration tests combining all three new saga features:
fail=True, resumes_from/should_resume, and default_timeout.

Includes the full OrderFulfillmentSaga from the plan document as an end-to-end test."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from pydomain.cqrs.saga.manager import SagaManager
from pydomain.cqrs.saga.registry import SagaRegistry
from pydomain.cqrs.saga.saga import Saga
from pydomain.cqrs.saga.state import SagaState, SagaStatus
from pydomain.testing.fake_saga_repository import FakeSagaRepository

from .conftest import (
    CancelPayment,
    ConfirmOrder,
    FraudReviewApproved,
    FraudReviewRejected,
    LogFraudFlag,
    NotifyCustomerOfCancellation,
    OrderCreated,
    OrderFulfillmentSaga,
    ProcessPayment,
    ReserveItems,
    ShipOrder,
    TransactionFlaggedForFraud,
    _noop_command_bus,
)

# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def saga_state() -> SagaState:
    return SagaState(saga_type="OrderFulfillmentSaga", correlation_id=uuid4())


@pytest.fixture
def fulfillment_saga(saga_state: SagaState) -> OrderFulfillmentSaga:
    return OrderFulfillmentSaga(saga_state)


@pytest.fixture
def fulfillment_repo() -> FakeSagaRepository:
    return FakeSagaRepository()


@pytest.fixture
def fulfillment_registry() -> SagaRegistry:
    registry = SagaRegistry()
    registry.register_saga(OrderFulfillmentSaga)
    return registry


@pytest.fixture
def fulfillment_manager(
    fulfillment_repo: FakeSagaRepository,
    fulfillment_registry: SagaRegistry,
) -> SagaManager:
    return SagaManager(
        repository=fulfillment_repo,
        registry=fulfillment_registry,
        command_bus=_noop_command_bus(),
    )


# ═══════════════════════════════════════════════════════════════════════
# OrderFulfillmentSaga — End-to-End Happy Path
# ═══════════════════════════════════════════════════════════════════════


class TestOrderFulfillmentSagaHappyPath:
    """Complete successful flow through the OrderFulfillmentSaga."""

    @pytest.mark.anyio
    async def test_full_happy_path_charge_and_confirm(
        self,
        fulfillment_saga: OrderFulfillmentSaga,
    ) -> None:
        """OrderCreated → charging → FraudReviewApproved → completed."""
        cid = uuid4()

        # Step 1: Customer places order → charge payment
        await fulfillment_saga.handle(
            OrderCreated(order_id="ORD-100", correlation_id=cid)
        )
        cmds1 = fulfillment_saga.collect_commands()
        assert len(cmds1) == 1
        assert isinstance(cmds1[0], ProcessPayment)
        assert cmds1[0].order_id == "ORD-100"
        assert len(fulfillment_saga.state.compensation_stack) == 1
        assert (
            fulfillment_saga.state.compensation_stack[0].command_type == "CancelPayment"
        )
        assert (
            "Refund customer for order ORD-100"
            in fulfillment_saga.state.compensation_stack[0].description
        )
        assert fulfillment_saga.state.current_step == "charging_customer"

        # Step 2: Transaction flagged for fraud → log and suspend
        await fulfillment_saga.handle(
            TransactionFlaggedForFraud(
                customer_id="CUST-1", risk_score=85, correlation_id=cid
            )
        )
        cmds2 = fulfillment_saga.collect_commands()
        assert len(cmds2) == 1
        assert isinstance(cmds2[0], LogFraudFlag)
        assert cmds2[0].risk_score == 85
        assert fulfillment_saga.state.status == SagaStatus.SUSPENDED
        assert fulfillment_saga.state.current_step == "logging_fraud_flag"
        assert "risk score 85" in (fulfillment_saga.state.suspension_reason or "")
        assert fulfillment_saga.state.timeout_at is not None  # 24h timeout

        # Step 3: Senior manager approves fraud review → resume and complete
        fulfillment_saga.state.status = SagaStatus.SUSPENDED  # simulate manager resume
        await fulfillment_saga.handle(
            FraudReviewApproved(
                order_id="ORD-100", agent_role="SENIOR_MANAGER", correlation_id=cid
            )
        )
        cmds3 = fulfillment_saga.collect_commands()
        assert len(cmds3) == 1
        assert isinstance(cmds3[0], ConfirmOrder)
        assert fulfillment_saga.state.status == SagaStatus.COMPLETED
        assert fulfillment_saga.state.compensation_stack == []


# ═══════════════════════════════════════════════════════════════════════
# OrderFulfillmentSaga — Unhappy Paths
# ═══════════════════════════════════════════════════════════════════════


class TestOrderFulfillmentSagaUnhappyPath:
    """Failure and edge-case scenarios for OrderFulfillmentSaga."""

    @pytest.mark.anyio
    async def test_fraud_rejected_fails_with_compensation(
        self,
        fulfillment_saga: OrderFulfillmentSaga,
    ) -> None:
        """FraudReviewRejected triggers declarative fail with compensation."""
        cid = uuid4()

        # Step 1: charge
        await fulfillment_saga.handle(
            OrderCreated(order_id="ORD-200", correlation_id=cid)
        )
        _ = fulfillment_saga.collect_commands()
        assert len(fulfillment_saga.state.compensation_stack) == 1

        # Step 2: fraud flag → suspend
        await fulfillment_saga.handle(
            TransactionFlaggedForFraud(
                customer_id="C2", risk_score=95, correlation_id=cid
            )
        )
        _ = fulfillment_saga.collect_commands()
        assert fulfillment_saga.state.status == SagaStatus.SUSPENDED

        # Step 3: FraudReviewRejected → declarative fail!
        fulfillment_saga.state.status = SagaStatus.SUSPENDED
        await fulfillment_saga.handle(
            FraudReviewRejected(
                order_id="ORD-200", agent_id="AGENT-7", correlation_id=cid
            )
        )
        assert fulfillment_saga.state.status == SagaStatus.COMPENSATING
        assert "Agent AGENT-7 rejected" in (fulfillment_saga.state.error or "")

        # Compensation collected: CancelPayment (from step 1)
        comp_cmds = fulfillment_saga.collect_commands()
        assert len(comp_cmds) == 1
        assert isinstance(comp_cmds[0], CancelPayment)

    @pytest.mark.anyio
    async def test_fraud_approved_by_junior_agent_is_blocked(
        self,
        fulfillment_saga: OrderFulfillmentSaga,
    ) -> None:
        """JUNIOR_AGENT's FraudReviewApproved blocked by should_resume predicate."""
        cid = uuid4()

        # Setup: charge then suspend for fraud
        await fulfillment_saga.handle(
            OrderCreated(order_id="ORD-300", correlation_id=cid)
        )
        _ = fulfillment_saga.collect_commands()
        await fulfillment_saga.handle(
            TransactionFlaggedForFraud(
                customer_id="C3", risk_score=80, correlation_id=cid
            )
        )
        _ = fulfillment_saga.collect_commands()

        # Verify should_resume blocks junior agent
        assert fulfillment_saga.state.status == SagaStatus.SUSPENDED
        result = fulfillment_saga.should_resume(
            FraudReviewApproved(order_id="ORD-300", agent_role="JUNIOR_AGENT")
        )
        assert result is False

        # Verify should_resume allows senior manager
        result = fulfillment_saga.should_resume(
            FraudReviewApproved(order_id="ORD-300", agent_role="SENIOR_MANAGER")
        )
        assert result is True

    @pytest.mark.anyio
    async def test_random_event_does_not_wake_suspended_saga(
        self,
        fulfillment_saga: OrderFulfillmentSaga,
    ) -> None:
        """Random events blocked by resumes_from when suspended."""
        cid = uuid4()

        await fulfillment_saga.handle(
            OrderCreated(order_id="ORD-400", correlation_id=cid)
        )
        _ = fulfillment_saga.collect_commands()
        await fulfillment_saga.handle(
            TransactionFlaggedForFraud(
                customer_id="C4", risk_score=70, correlation_id=cid
            )
        )
        _ = fulfillment_saga.collect_commands()

        assert fulfillment_saga.state.status == SagaStatus.SUSPENDED
        assert fulfillment_saga.state.current_step == "logging_fraud_flag"

        # OrderCreated is not authorized to resume from logging_fraud_flag
        result = fulfillment_saga.should_resume(
            OrderCreated(order_id="ORD-400", correlation_id=cid)
        )
        assert result is False

        # TransactionFlaggedForFraud is also not authorized
        result = fulfillment_saga.should_resume(
            TransactionFlaggedForFraud(
                customer_id="C4", risk_score=70, correlation_id=cid
            )
        )
        assert result is False


# ═══════════════════════════════════════════════════════════════════════
# OrderFulfillmentSaga — Manager Integration
# ═══════════════════════════════════════════════════════════════════════


class TestOrderFulfillmentSagaManagerIntegration:
    """OrderFulfillmentSaga driven through SagaManager (the real entry point)."""

    @pytest.mark.anyio
    async def test_full_lifecycle_through_manager(
        self,
        fulfillment_manager: SagaManager,
        fulfillment_repo: FakeSagaRepository,
    ) -> None:
        """Saga lifecycle through manager: create → suspend → resume → complete."""
        cid = uuid4()

        # Step 1: OrderCreated → charge payment
        await fulfillment_manager.handle(
            OrderCreated(order_id="ORD-500", correlation_id=cid)
        )
        state = await fulfillment_repo.find_by_correlation_id(
            cid, "OrderFulfillmentSaga"
        )
        assert state is not None
        assert state.status == SagaStatus.RUNNING
        assert state.current_step == "charging_customer"
        assert len(state.compensation_stack) == 1

        # Step 2: Fraud flag → suspend
        await fulfillment_manager.handle(
            TransactionFlaggedForFraud(
                customer_id="C5", risk_score=90, correlation_id=cid
            )
        )
        state = await fulfillment_repo.find_by_correlation_id(
            cid, "OrderFulfillmentSaga"
        )
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED
        assert state.current_step == "logging_fraud_flag"
        assert state.timeout_at is not None  # 24h

        # Step 3: FraudReviewApproved by SENIOR_MANAGER → resume and complete
        await fulfillment_manager.handle(
            FraudReviewApproved(
                order_id="ORD-500", agent_role="SENIOR_MANAGER", correlation_id=cid
            )
        )
        state = await fulfillment_repo.find_by_correlation_id(
            cid, "OrderFulfillmentSaga"
        )
        assert state is not None
        assert state.status == SagaStatus.COMPLETED
        assert state.compensation_stack == []

    @pytest.mark.anyio
    async def test_suspended_saga_ignores_blocked_event_through_manager(
        self,
        fulfillment_manager: SagaManager,
        fulfillment_repo: FakeSagaRepository,
    ) -> None:
        """When saga is suspended, events not authorized by resumes_from
        or blocked by should_resume do NOT wake it up through the manager."""
        cid = uuid4()

        # Drive to suspended state
        await fulfillment_manager.handle(
            OrderCreated(order_id="ORD-600", correlation_id=cid)
        )
        await fulfillment_manager.handle(
            TransactionFlaggedForFraud(
                customer_id="C6", risk_score=75, correlation_id=cid
            )
        )

        state = await fulfillment_repo.find_by_correlation_id(
            cid, "OrderFulfillmentSaga"
        )
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED

        # Try to send a new OrderCreated — should be ignored (wrong step)
        await fulfillment_manager.handle(
            OrderCreated(order_id="ORD-600-NEW", correlation_id=cid)
        )
        state = await fulfillment_repo.find_by_correlation_id(
            cid, "OrderFulfillmentSaga"
        )
        assert state is not None
        # Still SUSPENDED — event was rejected by should_resume
        assert state.status == SagaStatus.SUSPENDED
        # Step unchanged
        assert state.current_step == "logging_fraud_flag"

    @pytest.mark.anyio
    async def test_fraud_rejected_fails_through_manager(
        self,
        fulfillment_manager: SagaManager,
        fulfillment_repo: FakeSagaRepository,
    ) -> None:
        """FraudReviewRejected triggers declarative fail through the manager,
        with compensation dispatch."""
        cid = uuid4()

        # Drive to suspended
        await fulfillment_manager.handle(
            OrderCreated(order_id="ORD-700", correlation_id=cid)
        )
        await fulfillment_manager.handle(
            TransactionFlaggedForFraud(
                customer_id="C7", risk_score=88, correlation_id=cid
            )
        )

        state = await fulfillment_repo.find_by_correlation_id(
            cid, "OrderFulfillmentSaga"
        )
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED

        # FraudReviewRejected → declarative fail
        await fulfillment_manager.handle(
            FraudReviewRejected(
                order_id="ORD-700", agent_id="AGENT-42", correlation_id=cid
            )
        )

        state = await fulfillment_repo.find_by_correlation_id(
            cid, "OrderFulfillmentSaga"
        )
        assert state is not None
        # Compensation was dispatched → COMPENSATED
        assert state.is_terminal
        assert "Agent AGENT-42 rejected" in (state.error or "")


# ═══════════════════════════════════════════════════════════════════════
# Combined Features — Multiple Suspension Points
# ═══════════════════════════════════════════════════════════════════════


class TestCombinedFeaturesMultiSuspension:
    """Saga with multiple suspension points, each with distinct resumes_from config."""

    @pytest.mark.anyio
    async def test_two_suspension_points_with_isolated_wake_up_events(
        self,
        fulfillment_repo: FakeSagaRepository,
    ) -> None:
        """Two different suspension points, each only woken by their specific events."""
        from .conftest import PaymentDeclined

        class MultiSuspendSaga(Saga[SagaState]):
            default_timeout = timedelta(days=14)
            listens_to = [
                OrderCreated,
                TransactionFlaggedForFraud,
                PaymentDeclined,
                FraudReviewApproved,
                FraudReviewRejected,
            ]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: ProcessPayment(order_id=e.order_id),
                    step="payment_processing",
                    compensate=lambda e: CancelPayment(order_id=e.order_id),
                )
                # Suspension point A: fraud review
                self.on(
                    TransactionFlaggedForFraud,
                    send=lambda e: LogFraudFlag(
                        customer_id=e.customer_id, risk_score=e.risk_score
                    ),
                    step="fraud_review",
                    suspend=True,
                    suspend_reason="Fraud check required",
                    suspend_timeout=timedelta(hours=48),
                )
                # Suspension point B: payment declined (different reason)
                self.on(
                    PaymentDeclined,
                    send=lambda e: ReserveItems(order_id=e.order_id),
                    step="payment_retry",
                    suspend=True,
                    suspend_reason="Payment declined — manual intervention needed",
                )
                # Wake-up for suspension A only
                self.on(
                    FraudReviewApproved,
                    send=lambda e: ConfirmOrder(order_id=e.order_id),
                    step="confirming",
                    resumes_from="fraud_review",
                    complete=True,
                )

        registry = SagaRegistry()
        registry.register_saga(MultiSuspendSaga)
        mgr = SagaManager(
            repository=fulfillment_repo,
            registry=registry,
            command_bus=_noop_command_bus(),
        )

        cid = uuid4()

        # Setup: charge → fraud flag (suspend at fraud_review)
        await mgr.handle(OrderCreated(order_id="ORD-800", correlation_id=cid))
        await mgr.handle(
            TransactionFlaggedForFraud(
                customer_id="C8", risk_score=60, correlation_id=cid
            )
        )
        state = await fulfillment_repo.find_by_correlation_id(cid, "MultiSuspendSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED
        assert state.current_step == "fraud_review"

        # FraudReviewApproved IS authorized for fraud_review → resumes and completes
        await mgr.handle(
            FraudReviewApproved(
                order_id="ORD-800", agent_role="SENIOR_MANAGER", correlation_id=cid
            )
        )
        state = await fulfillment_repo.find_by_correlation_id(cid, "MultiSuspendSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPLETED

    @pytest.mark.anyio
    async def test_wrong_wake_up_event_blocked_at_second_suspension_point(
        self,
        fulfillment_repo: FakeSagaRepository,
    ) -> None:
        """At suspension point B (payment_retry), FraudReviewApproved is blocked."""
        from .conftest import PaymentDeclined

        class MultiSuspendSaga2(Saga[SagaState]):
            listens_to = [
                OrderCreated,
                PaymentDeclined,
                FraudReviewApproved,
            ]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: ProcessPayment(order_id=e.order_id),
                    step="payment_processing",
                    compensate=lambda e: CancelPayment(order_id=e.order_id),
                )
                self.on(
                    PaymentDeclined,
                    send=lambda e: ReserveItems(order_id=e.order_id),
                    step="payment_retry",
                    suspend=True,
                    suspend_reason="Manual payment retry",
                )
                self.on(
                    FraudReviewApproved,
                    send=lambda e: ConfirmOrder(order_id=e.order_id),
                    step="confirming",
                    resumes_from="fraud_review",  # NOT "payment_retry"!
                    complete=True,
                )

        registry = SagaRegistry()
        registry.register_saga(MultiSuspendSaga2)
        mgr = SagaManager(
            repository=fulfillment_repo,
            registry=registry,
            command_bus=_noop_command_bus(),
        )

        cid = uuid4()

        # Drive to payment_retry suspension
        await mgr.handle(OrderCreated(order_id="ORD-900", correlation_id=cid))
        await mgr.handle(
            PaymentDeclined(
                order_id="ORD-900", reason="Insufficient funds", correlation_id=cid
            )
        )

        state = await fulfillment_repo.find_by_correlation_id(cid, "MultiSuspendSaga2")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED
        assert state.current_step == "payment_retry"

        # FraudReviewApproved is registered for "fraud_review", not "payment_retry"
        await mgr.handle(FraudReviewApproved(order_id="ORD-900", correlation_id=cid))
        state = await fulfillment_repo.find_by_correlation_id(cid, "MultiSuspendSaga2")
        assert state is not None
        # Still SUSPENDED — wrong wake-up event for this step
        assert state.status == SagaStatus.SUSPENDED
        assert state.current_step == "payment_retry"


# ═══════════════════════════════════════════════════════════════════════
# Combined Features — fail + resumes_from on same event
# ═══════════════════════════════════════════════════════════════════════


class TestCombinedFeaturesFailWithResumesFrom:
    """Events that combine fail=True with resumes_from for step-gated failures."""

    @pytest.mark.anyio
    async def test_fail_with_resumes_from_only_from_specific_step(
        self, saga_state: SagaState
    ) -> None:
        """A fail event can only fail the saga when it's at the right step."""
        saga = Saga(saga_state)
        saga.on(
            OrderCreated,
            send=lambda e: ProcessPayment(order_id=e.order_id),
            step="charging",
            compensate=lambda e: CancelPayment(order_id=e.order_id),
        )
        saga.on(
            FraudReviewRejected,
            send=lambda e: NotifyCustomerOfCancellation(customer_id=e.order_id),
            resumes_from="fraud_check",
            fail=True,
            fail_reason="Fraud rejected",
        )

        # Drive to "charging" step
        await saga.handle(OrderCreated(order_id="ORD-1", correlation_id=uuid4()))
        _ = saga.collect_commands()
        assert saga.state.current_step == "charging"

        # FraudReviewRejected is NOT authorized for "charging"
        saga.state.status = SagaStatus.SUSPENDED
        result = saga.should_resume(FraudReviewRejected(order_id="ORD-1", agent_id="X"))
        assert result is False

        # But if we're at "fraud_check", it IS authorized
        saga.state.current_step = "fraud_check"
        result = saga.should_resume(FraudReviewRejected(order_id="ORD-1", agent_id="X"))
        assert result is True


# ═══════════════════════════════════════════════════════════════════════
# Combined Features — All Three Together
# ═══════════════════════════════════════════════════════════════════════


class TestAllThreeFeaturesCombined:
    """fail=True + resumes_from + should_resume + default_timeout on a single saga."""

    @pytest.mark.anyio
    async def test_full_combination_through_manager(
        self,
    ) -> None:
        """A saga using all three new features together, driven through the manager."""
        from pydomain.cqrs.saga.manager import SagaManager
        from pydomain.cqrs.saga.registry import SagaRegistry
        from pydomain.testing.fake_saga_repository import FakeSagaRepository

        class FullCombinationSaga(Saga[SagaState]):
            default_timeout = timedelta(days=7)
            listens_to = [
                OrderCreated,
                TransactionFlaggedForFraud,
                FraudReviewApproved,
                FraudReviewRejected,
            ]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                # Step 1: charge with 7-day timeout (from default_timeout)
                self.on(
                    OrderCreated,
                    send=lambda e: ProcessPayment(order_id=e.order_id),
                    step="charging",
                    compensate=lambda e: CancelPayment(order_id=e.order_id),
                    compensate_description=lambda e: f"Refund for {e.order_id}",
                )
                # Step 2: fraud suspend (24h override)
                self.on(
                    TransactionFlaggedForFraud,
                    send=lambda e: LogFraudFlag(
                        customer_id=e.customer_id, risk_score=e.risk_score
                    ),
                    step="fraud_review",
                    suspend=True,
                    suspend_reason=lambda e: f"Risk {e.risk_score}",
                    suspend_timeout=timedelta(hours=24),
                )
                # Step 3: approved by senior only, resumes from fraud_review
                self.on(
                    FraudReviewApproved,
                    send=lambda e: ConfirmOrder(order_id=e.order_id),
                    step="confirming",
                    resumes_from="fraud_review",
                    should_resume=lambda e: e.agent_role == "SENIOR_MANAGER",
                    complete=True,
                )
                # Step 4: rejected → declarative fail, also gated by fraud_review
                self.on(
                    FraudReviewRejected,
                    send=lambda e: NotifyCustomerOfCancellation(customer_id=e.order_id),
                    resumes_from="fraud_review",
                    fail=True,
                    fail_reason=lambda e: f"Rejected by {e.agent_id}",
                )

        repo = FakeSagaRepository()
        registry = SagaRegistry()
        registry.register_saga(FullCombinationSaga)
        mgr = SagaManager(
            repository=repo,
            registry=registry,
            command_bus=_noop_command_bus(),
        )

        cid = uuid4()

        # Happy path: charge → fraud → approve (senior) → complete
        await mgr.handle(OrderCreated(order_id="ORD-HAPPY", correlation_id=cid))
        await mgr.handle(
            TransactionFlaggedForFraud(
                customer_id="C1", risk_score=50, correlation_id=cid
            )
        )
        await mgr.handle(
            FraudReviewApproved(
                order_id="ORD-HAPPY", agent_role="SENIOR_MANAGER", correlation_id=cid
            )
        )

        state = await repo.find_by_correlation_id(cid, "FullCombinationSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPLETED
        assert state.compensation_stack == []

        # Unhappy path: charge → fraud → reject → fail with compensation
        cid2 = uuid4()
        await mgr.handle(OrderCreated(order_id="ORD-UNHAPPY", correlation_id=cid2))
        await mgr.handle(
            TransactionFlaggedForFraud(
                customer_id="C2", risk_score=95, correlation_id=cid2
            )
        )
        await mgr.handle(
            FraudReviewRejected(
                order_id="ORD-UNHAPPY", agent_id="AGENT-13", correlation_id=cid2
            )
        )

        state2 = await repo.find_by_correlation_id(cid2, "FullCombinationSaga")
        assert state2 is not None
        assert state2.is_terminal
        assert "Rejected by AGENT-13" in (state2.error or "")


# ═══════════════════════════════════════════════════════════════════════
# Multiple Suspensions — Sequential Suspend→Resume→Suspend→Resume
# ═══════════════════════════════════════════════════════════════════════


class TestSequentialMultipleSuspensions:
    """A single saga that suspends, resumes, suspends again, and resumes again
    — each suspension point with its own isolated resumes_from rules."""

    @pytest.mark.anyio
    async def test_suspend_resume_suspend_resume_full_lifecycle(
        self,
    ) -> None:
        """First suspension (fraud check) → resume → second suspension
        (inventory check) → resume → complete."""
        from pydomain.cqrs.saga.manager import SagaManager
        from pydomain.cqrs.saga.registry import SagaRegistry
        from pydomain.testing.fake_saga_repository import FakeSagaRepository

        from .conftest import (
            ApprovalGranted,
            ItemsReserved,
            ShipOrder,
        )

        class SequentialSuspendSaga(Saga[SagaState]):
            """Multi-department approval saga:
            charge → fraud→inventory→ship→complete."""

            listens_to = [
                OrderCreated,
                TransactionFlaggedForFraud,
                FraudReviewApproved,
                ItemsReserved,
                ApprovalGranted,
            ]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                # Step 1: Charge customer
                self.on(
                    OrderCreated,
                    send=lambda e: ProcessPayment(order_id=e.order_id),
                    step="charged",
                    compensate=lambda e: CancelPayment(order_id=e.order_id),
                )
                # Step 2: Fraud flagged — SUSPEND #1
                self.on(
                    TransactionFlaggedForFraud,
                    send=lambda e: LogFraudFlag(
                        customer_id=e.customer_id, risk_score=e.risk_score
                    ),
                    step="fraud_review",
                    suspend=True,
                    suspend_reason="Awaiting fraud team review",
                    suspend_timeout=timedelta(hours=24),
                )
                # Step 3a: Fraud approved — resumes, proceeds to inventory
                self.on(
                    FraudReviewApproved,
                    send=lambda e: ShipOrder(order_id=e.order_id),
                    step="shipping",
                    resumes_from="fraud_review",
                )
                # Step 3b: Alternative path — items reserved (inventory check)
                # SUSPEND #2
                self.on(
                    ItemsReserved,
                    send=lambda e: ShipOrder(order_id=e.order_id),
                    step="inventory_check",
                    suspend=True,
                    suspend_reason="Awaiting inventory confirmation",
                    suspend_timeout=timedelta(hours=4),
                )
                # Step 4: Final approval — resumes from inventory_check, completes
                self.on(
                    ApprovalGranted,
                    send=lambda e: ConfirmOrder(order_id=e.order_id),
                    step="completed",
                    resumes_from="inventory_check",
                    complete=True,
                )

        repo = FakeSagaRepository()
        registry = SagaRegistry()
        registry.register_saga(SequentialSuspendSaga)
        mgr = SagaManager(
            repository=repo,
            registry=registry,
            command_bus=_noop_command_bus(),
        )

        cid = uuid4()

        # Charge
        await mgr.handle(OrderCreated(order_id="ORD-SEQ", correlation_id=cid))
        state = await repo.find_by_correlation_id(cid, "SequentialSuspendSaga")
        assert state is not None
        assert state.status == SagaStatus.RUNNING
        assert state.current_step == "charged"

        # Fraud flag → SUSPEND #1 at "fraud_review"
        await mgr.handle(
            TransactionFlaggedForFraud(
                customer_id="C1", risk_score=70, correlation_id=cid
            )
        )
        state = await repo.find_by_correlation_id(cid, "SequentialSuspendSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED
        assert state.current_step == "fraud_review"

        # Fraud approved → RESUME #1, proceed to inventory
        await mgr.handle(FraudReviewApproved(order_id="ORD-SEQ", correlation_id=cid))
        state = await repo.find_by_correlation_id(cid, "SequentialSuspendSaga")
        assert state is not None
        assert state.status == SagaStatus.RUNNING
        assert state.current_step == "shipping"

        # Items reserved → SUSPEND #2 at "inventory_check"
        await mgr.handle(
            ItemsReserved(order_id="ORD-SEQ", item_count=5, correlation_id=cid)
        )
        state = await repo.find_by_correlation_id(cid, "SequentialSuspendSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED
        assert state.current_step == "inventory_check"

        # Approval granted → RESUME #2, complete
        await mgr.handle(
            ApprovalGranted(
                order_id="ORD-SEQ", approved_by="manager", correlation_id=cid
            )
        )
        state = await repo.find_by_correlation_id(cid, "SequentialSuspendSaga")
        assert state is not None
        assert state.status == SagaStatus.COMPLETED
        assert state.compensation_stack == []

    @pytest.mark.anyio
    async def test_first_suspension_event_blocked_at_second_suspension_point(
        self,
        fulfillment_repo: FakeSagaRepository,
    ) -> None:
        """After resuming from the first suspension and entering the second,
        the first suspension's wake-up events should NOT work.
        Tested at the should_resume() level for precise step-gating verification."""
        from .conftest import (
            ApprovalGranted,
            ItemsReserved,
        )

        class TwoSuspendSaga(Saga[SagaState]):
            listens_to = [
                OrderCreated,
                TransactionFlaggedForFraud,
                FraudReviewApproved,
                ItemsReserved,
                ApprovalGranted,
            ]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: ProcessPayment(order_id=e.order_id),
                    step="charged",
                )
                self.on(
                    TransactionFlaggedForFraud,
                    send=lambda e: LogFraudFlag(
                        customer_id=e.customer_id, risk_score=e.risk_score
                    ),
                    step="fraud_review",
                    suspend=True,
                    suspend_reason="Fraud check",
                )
                self.on(
                    FraudReviewApproved,
                    send=lambda e: ShipOrder(order_id=e.order_id),
                    step="shipping",
                    resumes_from="fraud_review",
                )
                self.on(
                    ItemsReserved,
                    send=lambda e: ShipOrder(order_id=e.order_id),
                    step="inventory_check",
                    suspend=True,
                    suspend_reason="Inventory check",
                )
                self.on(
                    ApprovalGranted,
                    send=lambda e: ConfirmOrder(order_id=e.order_id),
                    step="completed",
                    resumes_from="inventory_check",
                    complete=True,
                )

        # Test should_resume directly at each suspension point
        state = SagaState(
            saga_type="TwoSuspendSaga",
            correlation_id=uuid4(),
            status=SagaStatus.SUSPENDED,
        )
        saga = TwoSuspendSaga(state)

        # At fraud_review, FraudReviewApproved IS authorized
        saga.state.current_step = "fraud_review"
        assert saga.should_resume(FraudReviewApproved(order_id="ORD-1")) is True
        # But ItemsReserved is NOT (not in any resumes_from)
        assert saga.should_resume(ItemsReserved(order_id="ORD-1")) is False

        # At inventory_check, ApprovalGranted IS authorized
        saga.state.current_step = "inventory_check"
        assert saga.should_resume(ApprovalGranted(order_id="ORD-1")) is True
        # But FraudReviewApproved is NOT (registered for fraud_review only)
        assert saga.should_resume(FraudReviewApproved(order_id="ORD-1")) is False
        # And ItemsReserved is NOT
        assert saga.should_resume(ItemsReserved(order_id="ORD-1")) is False


# ═══════════════════════════════════════════════════════════════════════
# Multiple Resume Events for a Single Suspension Point
# ═══════════════════════════════════════════════════════════════════════


class TestMultipleResumeEventsForOneSuspension:
    """Multiple different events can all be authorized to wake up the SAME
    suspension point — each with their own predicate and action."""

    @pytest.mark.anyio
    async def test_two_resume_events_both_authorized_for_same_step(
        self,
        fulfillment_repo: FakeSagaRepository,
    ) -> None:
        """FraudReviewApproved AND OverrideApproved both wake up from 'fraud_review'."""
        from pydomain.ddd.domain_event import DomainEvent

        # A domain event for manager override
        class OverrideApproved(DomainEvent):
            order_id: str
            override_by: str = ""

        class MultiResumeSaga(Saga[SagaState]):
            listens_to = [
                OrderCreated,
                TransactionFlaggedForFraud,
                FraudReviewApproved,
                OverrideApproved,
            ]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: ProcessPayment(order_id=e.order_id),
                    step="charged",
                    compensate=lambda e: CancelPayment(order_id=e.order_id),
                )
                self.on(
                    TransactionFlaggedForFraud,
                    send=lambda e: LogFraudFlag(
                        customer_id=e.customer_id, risk_score=e.risk_score
                    ),
                    step="fraud_review",
                    suspend=True,
                    suspend_reason="Awaiting review",
                )
                # Resume event A: normal fraud approval (requires SENIOR_MANAGER)
                self.on(
                    FraudReviewApproved,
                    send=lambda e: ConfirmOrder(order_id=e.order_id),
                    step="approved_via_fraud",
                    resumes_from="fraud_review",
                    should_resume=lambda e: e.agent_role == "SENIOR_MANAGER",
                    complete=True,
                )
                # Resume event B: override approval (any manager, different action)
                self.on(
                    OverrideApproved,
                    send=lambda e: ConfirmOrder(order_id=e.order_id),
                    step="approved_via_override",
                    resumes_from="fraud_review",
                    should_resume=lambda e: e.override_by != "",
                    complete=True,
                )

        registry = SagaRegistry()
        registry.register_saga(MultiResumeSaga)
        mgr = SagaManager(
            repository=fulfillment_repo,
            registry=registry,
            command_bus=_noop_command_bus(),
        )

        cid = uuid4()

        # Drive to suspended at fraud_review
        await mgr.handle(OrderCreated(order_id="ORD-MR", correlation_id=cid))
        await mgr.handle(
            TransactionFlaggedForFraud(
                customer_id="C1", risk_score=80, correlation_id=cid
            )
        )

        state = await fulfillment_repo.find_by_correlation_id(cid, "MultiResumeSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED
        assert state.current_step == "fraud_review"

        # Both events are authorized for fraud_review
        saga = MultiResumeSaga(state)
        saga.state.status = SagaStatus.SUSPENDED
        assert (
            saga.should_resume(
                FraudReviewApproved(order_id="ORD-MR", agent_role="SENIOR_MANAGER")
            )
            is True
        )
        assert (
            saga.should_resume(
                OverrideApproved(order_id="ORD-MR", override_by="DIRECTOR")
            )
            is True
        )

        # But predicates can still block
        assert (
            saga.should_resume(
                FraudReviewApproved(order_id="ORD-MR", agent_role="JUNIOR_AGENT")
            )
            is False
        )
        assert (
            saga.should_resume(OverrideApproved(order_id="ORD-MR", override_by=""))
            is False
        )

    @pytest.mark.anyio
    async def test_multiple_resume_events_different_actions_on_resume(
        self,
        fulfillment_repo: FakeSagaRepository,
    ) -> None:
        """Two resume events for the same step dispatch different forward commands."""
        from pydomain.cqrs.commands import Command, EmptyCommandResult
        from pydomain.ddd.domain_event import DomainEvent
        from pydomain.testing import FakeUnitOfWork as Fuw

        class EscalateEvent(DomainEvent):
            order_id: str
            escalated_by: str = ""

        class EscalateToManager(Command[EmptyCommandResult]):
            order_id: str

        class MultiActionResumeSaga(Saga[SagaState]):
            listens_to = [
                OrderCreated,
                TransactionFlaggedForFraud,
                FraudReviewApproved,
                EscalateEvent,
            ]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: ProcessPayment(order_id=e.order_id),
                    step="charged",
                    compensate=lambda e: CancelPayment(order_id=e.order_id),
                )
                self.on(
                    TransactionFlaggedForFraud,
                    send=lambda e: LogFraudFlag(
                        customer_id=e.customer_id, risk_score=e.risk_score
                    ),
                    step="fraud_review",
                    suspend=True,
                    suspend_reason="Fraud review",
                )
                # Resume path A: approved → confirm order
                self.on(
                    FraudReviewApproved,
                    send=lambda e: ConfirmOrder(order_id=e.order_id),
                    step="confirmed",
                    resumes_from="fraud_review",
                    complete=True,
                )
                # Resume path B: escalated → escalate to manager, stay running
                self.on(
                    EscalateEvent,
                    send=lambda e: EscalateToManager(order_id=e.order_id),
                    step="escalated",
                    resumes_from="fraud_review",
                )

        registry = SagaRegistry()
        registry.register_saga(MultiActionResumeSaga)
        bus = _noop_command_bus()

        # Register the escalate command handler
        async def noop(cmd: Any, uow: Any = None) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register(EscalateToManager, noop, uow_factory=lambda: Fuw())
        mgr = SagaManager(
            repository=fulfillment_repo,
            registry=registry,
            command_bus=bus,
        )

        cid = uuid4()

        # Suspend at fraud_review
        await mgr.handle(OrderCreated(order_id="ORD-ACT", correlation_id=cid))
        await mgr.handle(
            TransactionFlaggedForFraud(
                customer_id="C1", risk_score=70, correlation_id=cid
            )
        )

        # Resume via escalation (not approval)
        await mgr.handle(
            EscalateEvent(
                order_id="ORD-ACT", escalated_by="supervisor", correlation_id=cid
            )
        )

        state = await fulfillment_repo.find_by_correlation_id(
            cid, "MultiActionResumeSaga"
        )
        assert state is not None
        assert (
            state.status == SagaStatus.RUNNING
        )  # NOT completed (escalate doesn't complete)
        assert state.current_step == "escalated"

    @pytest.mark.anyio
    async def test_multiple_resume_events_with_different_predicates_same_step(
        self,
    ) -> None:
        """Same suspension point, two resume events, each with its own predicate
        applying different business rules."""
        from pydomain.cqrs.saga.manager import SagaManager
        from pydomain.cqrs.saga.registry import SagaRegistry
        from pydomain.ddd.domain_event import DomainEvent
        from pydomain.testing.fake_saga_repository import FakeSagaRepository

        class DirectorOverride(DomainEvent):
            order_id: str
            director_id: str = ""
            department: str = ""

        class MultiPredicateResumeSaga(Saga[SagaState]):
            listens_to = [
                OrderCreated,
                TransactionFlaggedForFraud,
                FraudReviewApproved,
                DirectorOverride,
            ]

            def __init__(self, state: SagaState) -> None:
                super().__init__(state)
                self.on(
                    OrderCreated,
                    send=lambda e: ProcessPayment(order_id=e.order_id),
                    step="charged",
                    compensate=lambda e: CancelPayment(order_id=e.order_id),
                )
                self.on(
                    TransactionFlaggedForFraud,
                    send=lambda e: LogFraudFlag(
                        customer_id=e.customer_id, risk_score=e.risk_score
                    ),
                    step="fraud_review",
                    suspend=True,
                    suspend_reason="Fraud review",
                )
                # Resume A: fraud team — only SENIOR_MANAGER or above
                self.on(
                    FraudReviewApproved,
                    send=lambda e: ConfirmOrder(order_id=e.order_id),
                    step="approved_by_fraud_team",
                    resumes_from="fraud_review",
                    should_resume=lambda e: (
                        e.agent_role in ("SENIOR_MANAGER", "DIRECTOR")
                    ),
                    complete=True,
                )
                # Resume B: director override — only from COMPLIANCE department
                self.on(
                    DirectorOverride,
                    send=lambda e: ConfirmOrder(order_id=e.order_id),
                    step="approved_by_director",
                    resumes_from="fraud_review",
                    should_resume=lambda e: e.department == "COMPLIANCE",
                    complete=True,
                )

        repo = FakeSagaRepository()
        registry = SagaRegistry()
        registry.register_saga(MultiPredicateResumeSaga)
        mgr = SagaManager(
            repository=repo,
            registry=registry,
            command_bus=_noop_command_bus(),
        )

        cid = uuid4()

        # Suspend at fraud_review
        await mgr.handle(OrderCreated(order_id="ORD-PRED", correlation_id=cid))
        await mgr.handle(
            TransactionFlaggedForFraud(
                customer_id="C1", risk_score=88, correlation_id=cid
            )
        )

        # Load state and test predicates directly
        state = await repo.find_by_correlation_id(cid, "MultiPredicateResumeSaga")
        assert state is not None
        saga = MultiPredicateResumeSaga(state)
        saga.state.status = SagaStatus.SUSPENDED

        # Fraud team: SENIOR_MANAGER passes, JUNIOR_AGENT fails
        assert (
            saga.should_resume(
                FraudReviewApproved(order_id="ORD-PRED", agent_role="SENIOR_MANAGER")
            )
            is True
        )
        assert (
            saga.should_resume(
                FraudReviewApproved(order_id="ORD-PRED", agent_role="JUNIOR_AGENT")
            )
            is False
        )
        assert (
            saga.should_resume(
                FraudReviewApproved(order_id="ORD-PRED", agent_role="DIRECTOR")
            )
            is True
        )

        # Director override: COMPLIANCE passes, LEGAL fails
        assert (
            saga.should_resume(
                DirectorOverride(
                    order_id="ORD-PRED", director_id="D1", department="COMPLIANCE"
                )
            )
            is True
        )
        assert (
            saga.should_resume(
                DirectorOverride(
                    order_id="ORD-PRED", director_id="D2", department="LEGAL"
                )
            )
            is False
        )
