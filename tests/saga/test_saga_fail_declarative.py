"""Tests for Phase 1: fail=True declarative + callable reasons/descriptions."""

from __future__ import annotations

from uuid import uuid4

import pytest

from pydomain.cqrs.saga.exceptions import SagaConfigurationError
from pydomain.cqrs.saga.saga import Saga
from pydomain.cqrs.saga.state import SagaState, SagaStatus

from .conftest import (
    CallableReasonsSaga,
    FailSaga,
    FailSagaStaticReason,
    FraudReviewRejected,
    OrderCreated,
    ReserveItems,
    TransactionFlaggedForFraud,
    TwoStepSaga,
)

# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def saga_state() -> SagaState:
    return SagaState(saga_type="TestSaga", correlation_id=uuid4())


@pytest.fixture
def fail_saga(saga_state: SagaState) -> FailSaga:
    return FailSaga(saga_state)


@pytest.fixture
def fail_saga_static(saga_state: SagaState) -> FailSagaStaticReason:
    return FailSagaStaticReason(saga_state)


@pytest.fixture
def callable_reasons_saga(saga_state: SagaState) -> CallableReasonsSaga:
    return CallableReasonsSaga(saga_state)


# ═══════════════════════════════════════════════════════════════════════
# fail=True Declarative
# ═══════════════════════════════════════════════════════════════════════


class TestSagaFailDeclarative:
    """on(fail=True) declaratively fails the saga without an imperative handler."""

    @pytest.mark.anyio
    async def test_fail_true_with_static_reason(
        self, fail_saga_static: FailSagaStaticReason
    ) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await fail_saga_static.handle(event)
        assert fail_saga_static.state.status == SagaStatus.FAILED
        assert fail_saga_static.state.error == "Order permanently rejected"
        assert fail_saga_static.state.failed_at is not None

    @pytest.mark.anyio
    async def test_fail_true_dispatches_forward_command(
        self, fail_saga_static: FailSagaStaticReason
    ) -> None:
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await fail_saga_static.handle(event)
        commands = fail_saga_static.collect_commands()
        assert len(commands) == 1
        assert isinstance(commands[0], ReserveItems)

    @pytest.mark.anyio
    async def test_fail_true_with_callable_reason(self, fail_saga: FailSaga) -> None:
        # First handle OrderCreated to push a compensation onto the stack
        await fail_saga.handle(OrderCreated(order_id="ORD-1", correlation_id=uuid4()))
        _ = fail_saga.collect_commands()
        assert len(fail_saga.state.compensation_stack) == 1

        # Now fail via declarative fail=True with callable reason
        await fail_saga.handle(
            FraudReviewRejected(
                order_id="ORD-1", agent_id="A-123", correlation_id=uuid4()
            )
        )
        # Compensation stack is non-empty, so saga enters COMPENSATING
        assert fail_saga.state.status == SagaStatus.COMPENSATING
        assert "Agent A-123 rejected" in (fail_saga.state.error or "")

    @pytest.mark.anyio
    async def test_fail_true_without_compensation_goes_to_failed(
        self, fail_saga_static: FailSagaStaticReason
    ) -> None:
        # No prior step, so compensation stack is empty — goes straight to FAILED
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await fail_saga_static.handle(event)
        assert fail_saga_static.state.status == SagaStatus.FAILED
        assert "Order permanently rejected" in (fail_saga_static.state.error or "")

    @pytest.mark.anyio
    async def test_fail_true_default_reason(self, saga_state: SagaState) -> None:
        """When fail_reason is omitted, defaults to 'Saga failed'."""
        saga = Saga(saga_state)
        saga.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id="ORD-1"),
            fail=True,
        )
        await saga.handle(OrderCreated(order_id="ORD-1", correlation_id=uuid4()))
        assert saga.state.status == SagaStatus.FAILED
        assert saga.state.error == "Saga failed"


# ═══════════════════════════════════════════════════════════════════════
# fail + complete/suspend Mutual Exclusion
# ═══════════════════════════════════════════════════════════════════════


class TestSagaFailMutualExclusion:
    """fail=True is mutually exclusive with complete=True and suspend=True."""

    def test_fail_and_complete_raises(self, saga_state: SagaState) -> None:
        saga = TwoStepSaga(saga_state)
        with pytest.raises(
            SagaConfigurationError,
            match="Cannot set both 'fail' and 'complete'",
        ):
            saga.on(
                OrderCreated,
                send=lambda e: ReserveItems(order_id="x"),
                fail=True,
                complete=True,
            )

    def test_fail_and_suspend_raises(self, saga_state: SagaState) -> None:
        saga = TwoStepSaga(saga_state)
        with pytest.raises(
            SagaConfigurationError,
            match="Cannot set both 'fail' and 'suspend'",
        ):
            saga.on(
                OrderCreated,
                send=lambda e: ReserveItems(order_id="x"),
                fail=True,
                suspend=True,
            )


# ═══════════════════════════════════════════════════════════════════════
# Callable Reasons / Descriptions
# ═══════════════════════════════════════════════════════════════════════


class TestSagaCallableReasons:
    """Callables for compensate_description and suspend_reason from events."""

    @pytest.mark.anyio
    async def test_callable_compensate_description(
        self, callable_reasons_saga: CallableReasonsSaga
    ) -> None:
        event = OrderCreated(order_id="ORD-42", correlation_id=uuid4())
        await callable_reasons_saga.handle(event)
        assert len(callable_reasons_saga.state.compensation_stack) == 1
        rec = callable_reasons_saga.state.compensation_stack[0]
        assert rec.description == "Cancel reservation for ORD-42"

    @pytest.mark.anyio
    async def test_callable_suspend_reason(
        self, callable_reasons_saga: CallableReasonsSaga
    ) -> None:
        # First step: reserve
        await callable_reasons_saga.handle(
            OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        )
        _ = callable_reasons_saga.collect_commands()

        # Second step: suspend with dynamic reason
        await callable_reasons_saga.handle(
            TransactionFlaggedForFraud(
                customer_id="C1", risk_score=85, correlation_id=uuid4()
            )
        )
        assert callable_reasons_saga.state.status == SagaStatus.SUSPENDED
        assert "risk score 85" in (callable_reasons_saga.state.suspension_reason or "")

    @pytest.mark.anyio
    async def test_static_compensate_description_still_works(
        self, saga_state: SagaState
    ) -> None:
        """Backward compatibility: static string compensate_description still works."""
        saga = Saga(saga_state)
        saga.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            compensate=lambda e: ReserveItems(order_id=e.order_id),
            compensate_description="Static description",
        )
        await saga.handle(OrderCreated(order_id="ORD-1", correlation_id=uuid4()))
        assert saga.state.compensation_stack[0].description == "Static description"
