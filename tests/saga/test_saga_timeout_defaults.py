"""Tests for Phase 3: Global timeouts with step-level overrides."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from pydomain.cqrs.saga.state import SagaState, SagaStatus

from .conftest import (
    ApprovalGranted,
    DefaultTimeoutSaga,
    OrderCreated,
    SuspendableSaga,
    TransactionFlaggedForFraud,
)

# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def saga_state() -> SagaState:
    return SagaState(saga_type="TestSaga", correlation_id=uuid4())


@pytest.fixture
def default_timeout_saga(saga_state: SagaState) -> DefaultTimeoutSaga:
    return DefaultTimeoutSaga(saga_state)


# ═══════════════════════════════════════════════════════════════════════
# Default Timeout
# ═══════════════════════════════════════════════════════════════════════


class TestSagaDefaultTimeout:
    """default_timeout provides a global fallback for suspension timeouts."""

    @pytest.mark.anyio
    async def test_default_timeout_applied_when_omitted(
        self, default_timeout_saga: DefaultTimeoutSaga
    ) -> None:
        await default_timeout_saga.handle(
            OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        )
        assert default_timeout_saga.state.status == SagaStatus.SUSPENDED
        assert default_timeout_saga.state.timeout_at is not None
        # The global default is 7 days
        now = datetime.now(UTC)
        assert default_timeout_saga.state.timeout_at is not None
        delta = default_timeout_saga.state.timeout_at - now  # type: ignore[operator]
        assert timedelta(days=6, hours=23) < delta < timedelta(days=7, hours=1)

    @pytest.mark.anyio
    async def test_step_timeout_overrides_default(
        self, default_timeout_saga: DefaultTimeoutSaga
    ) -> None:
        await default_timeout_saga.handle(
            TransactionFlaggedForFraud(
                customer_id="C1", risk_score=50, correlation_id=uuid4()
            )
        )
        assert default_timeout_saga.state.status == SagaStatus.SUSPENDED
        assert default_timeout_saga.state.timeout_at is not None
        # Step override is 24 hours
        now = datetime.now(UTC)
        delta = default_timeout_saga.state.timeout_at - now  # type: ignore[operator]
        assert timedelta(hours=23) < delta < timedelta(hours=25)

    @pytest.mark.anyio
    async def test_explicit_none_overrides_default(
        self, default_timeout_saga: DefaultTimeoutSaga
    ) -> None:
        await default_timeout_saga.handle(
            ApprovalGranted(order_id="ORD-1", correlation_id=uuid4())
        )
        assert default_timeout_saga.state.status == SagaStatus.SUSPENDED
        # Explicit None overrides the global default — infinite suspension
        assert default_timeout_saga.state.timeout_at is None

    @pytest.mark.anyio
    async def test_no_default_timeout_backward_compatible(self) -> None:
        """Sagas without default_timeout with explicit suspend_timeout still work."""
        state = SagaState(saga_type="SuspendableSaga", correlation_id=uuid4())
        saga = SuspendableSaga(state)
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        assert saga.state.status == SagaStatus.SUSPENDED
        # SuspendableSaga provides an explicit suspend_timeout=timedelta(hours=24)
        assert saga.state.timeout_at is not None

    @pytest.mark.anyio
    async def test_multiple_steps_each_correct_timeout(
        self, default_timeout_saga: DefaultTimeoutSaga
    ) -> None:
        """Each step independently resolves its timeout correctly."""
        # Step 1: uses global default (7 days)
        await default_timeout_saga.handle(
            OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        )
        _ = default_timeout_saga.collect_commands()
        timeout1 = default_timeout_saga.state.timeout_at

        # Reset state and do step 2: uses 24h override
        default_timeout_saga.state.status = SagaStatus.RUNNING
        await default_timeout_saga.handle(
            TransactionFlaggedForFraud(
                customer_id="C1", risk_score=50, correlation_id=uuid4()
            )
        )
        _ = default_timeout_saga.collect_commands()
        timeout2 = default_timeout_saga.state.timeout_at

        # Reset and do step 3: uses explicit None
        default_timeout_saga.state.status = SagaStatus.RUNNING
        await default_timeout_saga.handle(
            ApprovalGranted(order_id="ORD-1", correlation_id=uuid4())
        )
        timeout3 = default_timeout_saga.state.timeout_at

        # timeout1 (7 days) should be much later than timeout2 (24h)
        assert timeout1 is not None
        assert timeout2 is not None
        assert timeout3 is None
        assert (timeout1 - timeout2) > timedelta(days=5)  # type: ignore[operator]
