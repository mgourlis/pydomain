"""Tests for Phase 2: resumes_from and should_resume declarative step config."""

from __future__ import annotations

from uuid import uuid4

import pytest

from pydomain.cqrs.saga.state import SagaState, SagaStatus

from .conftest import (
    FraudReviewApproved,
    OrderCreated,
    ResumeFromMultipleSaga,
    ResumeFromSaga,
    ShouldResumePredicateSaga,
    TwoStepSaga,
)

# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def saga_state() -> SagaState:
    return SagaState(saga_type="TestSaga", correlation_id=uuid4())


@pytest.fixture
def resume_from_saga(saga_state: SagaState) -> ResumeFromSaga:
    return ResumeFromSaga(saga_state)


@pytest.fixture
def resume_from_multi_saga(saga_state: SagaState) -> ResumeFromMultipleSaga:
    return ResumeFromMultipleSaga(saga_state)


@pytest.fixture
def predicate_saga(saga_state: SagaState) -> ShouldResumePredicateSaga:
    return ShouldResumePredicateSaga(saga_state)


# ═══════════════════════════════════════════════════════════════════════
# resumes_from
# ═══════════════════════════════════════════════════════════════════════


class TestSagaResumesFrom:
    """resumes_from restricts which steps an event can wake up from."""

    def test_resumes_from_allows_matching_step(
        self, resume_from_saga: ResumeFromSaga
    ) -> None:
        resume_from_saga.state.status = SagaStatus.SUSPENDED
        resume_from_saga.state.current_step = "logging_fraud_flag"
        result = resume_from_saga.should_resume(FraudReviewApproved(order_id="ORD-1"))
        assert result is True

    def test_resumes_from_blocks_non_matching_step(
        self, resume_from_saga: ResumeFromSaga
    ) -> None:
        resume_from_saga.state.status = SagaStatus.SUSPENDED
        resume_from_saga.state.current_step = "reserving"
        result = resume_from_saga.should_resume(FraudReviewApproved(order_id="ORD-1"))
        assert result is False

    def test_resumes_from_with_list_multiple_steps(
        self, resume_from_multi_saga: ResumeFromMultipleSaga
    ) -> None:
        # Should allow resume from fraud_check
        resume_from_multi_saga.state.status = SagaStatus.SUSPENDED
        resume_from_multi_saga.state.current_step = "fraud_check"
        assert (
            resume_from_multi_saga.should_resume(FraudReviewApproved(order_id="ORD-1"))
            is True
        )

        # Should allow resume from payment_check
        resume_from_multi_saga.state.current_step = "payment_check"
        assert (
            resume_from_multi_saga.should_resume(FraudReviewApproved(order_id="ORD-1"))
            is True
        )

        # Should block unknown step
        resume_from_multi_saga.state.current_step = "some_other_step"
        assert (
            resume_from_multi_saga.should_resume(FraudReviewApproved(order_id="ORD-1"))
            is False
        )

    def test_event_not_in_resume_map_but_map_is_non_empty(
        self, resume_from_saga: ResumeFromSaga
    ) -> None:
        """Event not in _resume_map at all + map is non-empty → blocked."""
        resume_from_saga.state.status = SagaStatus.SUSPENDED
        resume_from_saga.state.current_step = "logging_fraud_flag"
        # OrderCreated is not registered in resume_from_saga's _resume_map
        result = resume_from_saga.should_resume(OrderCreated(order_id="ORD-1"))
        assert result is False

    def test_no_resumes_from_unrestricted(self, saga_state: SagaState) -> None:
        """Saga with no resumes_from registrations is unrestricted (backward compat)."""
        saga = TwoStepSaga(saga_state)
        saga.state.status = SagaStatus.SUSPENDED
        # No resumes_from registered in TwoStepSaga, so _resume_map is empty
        result = saga.should_resume(OrderCreated(order_id="ORD-1"))
        assert result is True


# ═══════════════════════════════════════════════════════════════════════
# should_resume Inline Predicate
# ═══════════════════════════════════════════════════════════════════════


class TestSagaShouldResumePredicate:
    """Inline should_resume predicate enables complex per-event resume logic."""

    def test_predicate_allows_matching_condition(
        self, predicate_saga: ShouldResumePredicateSaga
    ) -> None:
        predicate_saga.state.status = SagaStatus.SUSPENDED
        predicate_saga.state.current_step = "logging_fraud_flag"
        result = predicate_saga.should_resume(
            FraudReviewApproved(order_id="ORD-1", agent_role="SENIOR_MANAGER")
        )
        assert result is True

    def test_predicate_blocks_non_matching_condition(
        self, predicate_saga: ShouldResumePredicateSaga
    ) -> None:
        predicate_saga.state.status = SagaStatus.SUSPENDED
        predicate_saga.state.current_step = "logging_fraud_flag"
        result = predicate_saga.should_resume(
            FraudReviewApproved(order_id="ORD-1", agent_role="JUNIOR_AGENT")
        )
        assert result is False

    def test_predicate_wrong_step_blocked_by_resumes_from_first(
        self, predicate_saga: ShouldResumePredicateSaga
    ) -> None:
        """resumes_from check runs first — wrong step blocks before predicate."""
        predicate_saga.state.status = SagaStatus.SUSPENDED
        predicate_saga.state.current_step = "reserving"
        # Even with SENIOR_MANAGER, wrong step blocks first
        result = predicate_saga.should_resume(
            FraudReviewApproved(order_id="ORD-1", agent_role="SENIOR_MANAGER")
        )
        assert result is False
