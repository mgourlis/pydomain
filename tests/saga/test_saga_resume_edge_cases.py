"""Edge-case and unhappy-path tests for resumes_from and should_resume features."""

from __future__ import annotations

from uuid import uuid4

import pytest

from pydomain.cqrs.saga.saga import Saga
from pydomain.cqrs.saga.state import SagaState, SagaStatus

from .conftest import (
    FraudReviewApproved,
    OrderCreated,
    ReserveItems,
    ShouldResumeOverrideSaga,
)

# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def saga_state() -> SagaState:
    return SagaState(saga_type="TestSaga", correlation_id=uuid4())


@pytest.fixture
def override_saga(saga_state: SagaState) -> ShouldResumeOverrideSaga:
    return ShouldResumeOverrideSaga(saga_state)


# ═══════════════════════════════════════════════════════════════════════
# resumes_from Edge Cases
# ═══════════════════════════════════════════════════════════════════════


class TestResumesFromEdgeCases:
    """Edge cases and error paths for resumes_from step authorization."""

    # ── Multiple events, each with different resumes_from ───────────

    def test_multiple_events_different_resume_steps(
        self, saga_state: SagaState
    ) -> None:
        """Two different events are each authorized for different steps."""
        from .conftest import FraudReviewRejected

        saga = Saga(saga_state)
        saga.on(
            FraudReviewApproved,
            send=lambda e: ReserveItems(order_id="x"),
            resumes_from="fraud_check",
        )
        saga.on(
            FraudReviewRejected,
            send=lambda e: ReserveItems(order_id="x"),
            resumes_from="fraud_check",
        )
        saga.state.status = SagaStatus.SUSPENDED

        # Both events authorized for "fraud_check"
        saga.state.current_step = "fraud_check"
        assert saga.should_resume(FraudReviewApproved(order_id="ORD-1")) is True
        assert saga.should_resume(FraudReviewRejected(order_id="ORD-1")) is True

        # Neither authorized for "payment_check"
        saga.state.current_step = "payment_check"
        assert saga.should_resume(FraudReviewApproved(order_id="ORD-1")) is False
        assert saga.should_resume(FraudReviewRejected(order_id="ORD-1")) is False

    # ── Event in _resume_map with empty set ─────────────────────────

    def test_event_in_map_with_empty_set_still_blocked(
        self, saga_state: SagaState
    ) -> None:
        """If an event type has an empty allowed_steps set, it's blocked everywhere."""
        saga = Saga(saga_state)
        # Manually seed _resume_map with an empty set
        saga._resume_map[FraudReviewApproved] = set()
        saga.state.status = SagaStatus.SUSPENDED
        saga.state.current_step = "any_step"

        result = saga.should_resume(FraudReviewApproved(order_id="ORD-1"))
        assert result is False

    # ── resumes_from with duplicate step names ──────────────────────

    def test_resumes_from_duplicates_are_idempotent(
        self, saga_state: SagaState
    ) -> None:
        """Registering the same step multiple times doesn't change behavior."""
        saga = Saga(saga_state)
        saga.on(
            FraudReviewApproved,
            send=lambda e: ReserveItems(order_id="x"),
            resumes_from="step_a",
        )
        # Register same step again (e.g. in a different .on() call)
        saga.on(
            FraudReviewApproved,
            send=lambda e: ReserveItems(order_id="x"),
            resumes_from="step_a",
        )
        saga.state.status = SagaStatus.SUSPENDED

        saga.state.current_step = "step_a"
        assert saga.should_resume(FraudReviewApproved(order_id="ORD-1")) is True

        saga.state.current_step = "step_b"
        assert saga.should_resume(FraudReviewApproved(order_id="ORD-1")) is False

    # ── resumes_from list with mixed valid/invalid types ────────────

    def test_resumes_from_empty_list_blocks_all_steps(
        self, saga_state: SagaState
    ) -> None:
        """An empty list creates a mapping entry with no authorized steps,
        meaning the event can never resume the saga."""
        saga = Saga(saga_state)
        saga.on(
            FraudReviewApproved,
            send=lambda e: ReserveItems(order_id="x"),
            resumes_from=[],
        )
        saga.state.status = SagaStatus.SUSPENDED
        saga.state.current_step = "any_step"

        # Empty list → event is in _resume_map with empty set → blocked everywhere
        result = saga.should_resume(FraudReviewApproved(order_id="ORD-1"))
        assert result is False


# ═══════════════════════════════════════════════════════════════════════
# should_resume Predicate Edge Cases
# ═══════════════════════════════════════════════════════════════════════


class TestShouldResumePredicateEdgeCases:
    """Edge cases for the inline should_resume predicate."""

    # ── Predicate without resumes_from ──────────────────────────────

    def test_predicate_only_no_step_restriction(self, saga_state: SagaState) -> None:
        """should_resume without resumes_from applies to all steps."""
        saga = Saga(saga_state)
        saga.on(
            FraudReviewApproved,
            send=lambda e: ReserveItems(order_id="x"),
            should_resume=lambda e: e.agent_role == "SENIOR_MANAGER",
        )
        saga.state.status = SagaStatus.SUSPENDED

        # Any step — predicate governs
        saga.state.current_step = "any_step"
        assert (
            saga.should_resume(
                FraudReviewApproved(order_id="ORD-1", agent_role="SENIOR_MANAGER")
            )
            is True
        )
        assert (
            saga.should_resume(
                FraudReviewApproved(order_id="ORD-1", agent_role="JUNIOR_AGENT")
            )
            is False
        )

    # ── Predicate returning non-bool ────────────────────────────────

    def test_predicate_returning_truthy_value(self, saga_state: SagaState) -> None:
        """A predicate returning a truthy non-bool is accepted by Python truthiness."""
        saga = Saga(saga_state)
        saga.on(
            FraudReviewApproved,
            send=lambda e: ReserveItems(order_id="x"),
            should_resume=lambda e: 1,  # truthy int
        )
        saga.state.status = SagaStatus.SUSPENDED
        result = saga.should_resume(FraudReviewApproved(order_id="ORD-1"))
        assert result == 1  # returned as-is

    def test_predicate_returning_falsy_value(self, saga_state: SagaState) -> None:
        """A predicate returning a falsy non-bool blocks resume."""
        saga = Saga(saga_state)
        saga.on(
            FraudReviewApproved,
            send=lambda e: ReserveItems(order_id="x"),
            should_resume=lambda e: 0,  # falsy int
        )
        saga.state.status = SagaStatus.SUSPENDED
        result = saga.should_resume(FraudReviewApproved(order_id="ORD-1"))
        assert result == 0

    # ── Predicate receiving different event type ────────────────────

    def test_predicate_only_registered_for_specific_event_type(
        self, saga_state: SagaState
    ) -> None:
        """The predicate is only invoked for its registered event type."""
        saga = Saga(saga_state)
        saga.on(
            FraudReviewApproved,
            send=lambda e: ReserveItems(order_id="x"),
            should_resume=lambda e: False,  # always block
        )
        saga.state.status = SagaStatus.SUSPENDED
        saga.state.current_step = "any_step"

        # This event type has a predicate (always False)
        assert saga.should_resume(FraudReviewApproved(order_id="ORD-1")) is False

        # This event type has NO predicate — falls through to True
        assert saga.should_resume(OrderCreated(order_id="ORD-1")) is True


# ═══════════════════════════════════════════════════════════════════════
# should_resume() Subclass Override Bypass
# ═══════════════════════════════════════════════════════════════════════


class TestShouldResumeOverrideBypass:
    """Subclass override of should_resume() completely bypasses base logic."""

    def test_override_allows_event_that_base_would_block(
        self, override_saga: ShouldResumeOverrideSaga
    ) -> None:
        """Base logic would block OrderCreated (not in _resume_map + map non-empty),
        but the override returns True for everything."""
        override_saga.state.status = SagaStatus.SUSPENDED
        override_saga.state.current_step = "logging_fraud_flag"

        # Base logic would return False: OrderCreated not in _resume_map
        result = override_saga.should_resume(OrderCreated(order_id="ORD-1"))
        assert result is True

    def test_override_allows_even_when_predicate_would_block(
        self, override_saga: ShouldResumeOverrideSaga
    ) -> None:
        """The override ignores the should_resume predicate on FraudReviewApproved."""
        override_saga.state.status = SagaStatus.SUSPENDED
        override_saga.state.current_step = "logging_fraud_flag"

        # Base logic: resumes_from allows "logging_fraud_flag" but predicate
        # requires SENIOR_MANAGER. Override bypasses all of this.
        result = override_saga.should_resume(
            FraudReviewApproved(order_id="ORD-1", agent_role="JUNIOR_AGENT")
        )
        assert result is True


# ═══════════════════════════════════════════════════════════════════════
# should_resume + Manager Integration
# ═══════════════════════════════════════════════════════════════════════


class TestShouldResumeManagerIntegration:
    """should_resume() is called by SagaManager._process_saga for SUSPENDED sagas."""

    @pytest.mark.anyio
    async def test_suspended_saga_not_resumed_when_should_resume_returns_false(
        self,
    ) -> None:
        """Manager calls should_resume; if False, saga stays SUSPENDED and
        the event is effectively ignored for that saga."""
        from pydomain.cqrs.saga.manager import SagaManager
        from pydomain.cqrs.saga.registry import SagaRegistry
        from pydomain.testing.fake_saga_repository import FakeSagaRepository

        from .conftest import (
            ResumeFromSaga,
            _noop_command_bus,
        )
        from .conftest import (
            TransactionFlaggedForFraud as TFFF,
        )

        repo = FakeSagaRepository()
        registry = SagaRegistry()
        registry.register_saga(ResumeFromSaga)
        bus = _noop_command_bus()
        mgr = SagaManager(repository=repo, registry=registry, command_bus=bus)

        cid = uuid4()
        # Step 1: OrderCreated → suspends at "logging_fraud_flag"
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))

        # Step 2: TransactionFlaggedForFraud (which triggers the suspend)
        # ResumeFromSaga suspends on TransactionFlaggedForFraud, not OrderCreated.
        # OrderCreated step="reserving" (no suspend),
        # TransactionFlaggedForFraud step="logging_fraud_flag" suspend=True.
        await mgr.handle(TFFF(customer_id="C1", risk_score=50, correlation_id=cid))

        state = await repo.find_by_correlation_id(cid, "ResumeFromSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED
        assert state.current_step == "logging_fraud_flag"

        # Now send an event that should NOT resume: OrderCreated
        # ResumeFromSaga: FraudReviewApproved has resumes_from="logging_fraud_flag"
        # OrderCreated has no resumes_from → blocked when map is non-empty
        await mgr.handle(OrderCreated(order_id="ORD-2", correlation_id=cid))

        state = await repo.find_by_correlation_id(cid, "ResumeFromSaga")
        assert state is not None
        # Still SUSPENDED — should_resume returned False
        assert state.status == SagaStatus.SUSPENDED
        assert state.current_step == "logging_fraud_flag"

    @pytest.mark.anyio
    async def test_suspended_saga_resumed_when_should_resume_returns_true(
        self,
    ) -> None:
        """Manager resumes the saga when should_resume returns True."""
        from pydomain.cqrs.saga.manager import SagaManager
        from pydomain.cqrs.saga.registry import SagaRegistry
        from pydomain.testing.fake_saga_repository import FakeSagaRepository

        from .conftest import (
            FraudReviewApproved as FRA,
        )
        from .conftest import (
            ResumeFromSaga,
            _noop_command_bus,
        )
        from .conftest import (
            TransactionFlaggedForFraud as TFFF,
        )

        repo = FakeSagaRepository()
        registry = SagaRegistry()
        registry.register_saga(ResumeFromSaga)
        bus = _noop_command_bus()
        mgr = SagaManager(repository=repo, registry=registry, command_bus=bus)

        cid = uuid4()
        # Step 1: reserve
        await mgr.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
        # Step 2: suspend for fraud
        await mgr.handle(TFFF(customer_id="C1", risk_score=50, correlation_id=cid))

        state = await repo.find_by_correlation_id(cid, "ResumeFromSaga")
        assert state is not None
        assert state.status == SagaStatus.SUSPENDED

        # Step 3: FraudReviewApproved IS authorized for "logging_fraud_flag"
        await mgr.handle(FRA(order_id="ORD-1", correlation_id=cid))

        state = await repo.find_by_correlation_id(cid, "ResumeFromSaga")
        assert state is not None
        # Saga completed because FraudReviewApproved step has complete=True
        assert state.status == SagaStatus.COMPLETED
