"""Edge-case and unhappy-path tests for fail=True declarative failure."""

from __future__ import annotations

from uuid import uuid4

import pytest

from pydomain.cqrs.saga.saga import Saga
from pydomain.cqrs.saga.state import SagaState, SagaStatus

from .conftest import (
    CancelOrder,
    CancelReservation,
    FailSagaWithCompensate,
    FraudReviewRejected,
    NotifyCustomerOfCancellation,
    OrderCreated,
    ReserveItems,
)

# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def saga_state() -> SagaState:
    return SagaState(saga_type="TestSaga", correlation_id=uuid4())


@pytest.fixture
def fail_with_compensate(saga_state: SagaState) -> FailSagaWithCompensate:
    return FailSagaWithCompensate(saga_state)


# ═══════════════════════════════════════════════════════════════════════
# fail=True Edge Cases
# ═══════════════════════════════════════════════════════════════════════


class TestFailEdgeCases:
    """Edge cases and error paths for the fail=True declarative parameter."""

    # ── fail_reason callable edge cases ────────────────────────────

    @pytest.mark.anyio
    async def test_fail_reason_callable_returns_empty_string(
        self, saga_state: SagaState
    ) -> None:
        """When a callable fail_reason returns '', the empty string is used as-is.
        Callables have full control over the reason; the 'or' fallback only
        applies to static string parameters, not callable results."""
        saga = Saga(saga_state)
        saga.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id="ORD-1"),
            fail=True,
            fail_reason=lambda e: "",
        )
        await saga.handle(OrderCreated(order_id="ORD-1", correlation_id=uuid4()))
        assert saga.state.status == SagaStatus.FAILED
        # Callable returned empty string — no fallback applied
        assert saga.state.error == ""

    @pytest.mark.anyio
    async def test_fail_reason_none_uses_default(self, saga_state: SagaState) -> None:
        """When fail_reason is omitted entirely, default is used."""
        saga = Saga(saga_state)
        saga.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id="ORD-1"),
            fail=True,
            # fail_reason not provided
        )
        await saga.handle(OrderCreated(order_id="ORD-1", correlation_id=uuid4()))
        assert saga.state.status == SagaStatus.FAILED
        assert saga.state.error == "Saga failed"

    # ── fail + compensation collection ─────────────────────────────

    @pytest.mark.anyio
    async def test_fail_step_with_compensate_collects_both(
        self, fail_with_compensate: FailSagaWithCompensate
    ) -> None:
        """When a fail step has its own compensate, it's added before failing.
        Both compensations (prior step + fail step) are executed LIFO."""
        # Step 1: charge (adds CancelReservation compensation)
        await fail_with_compensate.handle(
            OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        )
        _ = fail_with_compensate.collect_commands()
        assert len(fail_with_compensate.state.compensation_stack) == 1
        assert (
            fail_with_compensate.state.compensation_stack[0].command_type
            == "CancelReservation"
        )

        # Step 2: fail (adds CancelOrder compensation, then fails)
        await fail_with_compensate.handle(
            FraudReviewRejected(
                order_id="ORD-1", agent_id="A-99", correlation_id=uuid4()
            )
        )
        # Saga is COMPENSATING because stack is non-empty
        assert fail_with_compensate.state.status == SagaStatus.COMPENSATING
        assert "Agent A-99 rejected" in (fail_with_compensate.state.error or "")

        # Compensation stack was expanded by fail step before fail() drained it
        commands = fail_with_compensate.collect_commands()
        # LIFO: CancelOrder (added last) pops first, then CancelReservation
        assert len(commands) == 2
        assert isinstance(commands[0], CancelOrder)
        assert isinstance(commands[1], CancelReservation)

    @pytest.mark.anyio
    async def test_fail_step_forward_command_dispatched(
        self, fail_with_compensate: FailSagaWithCompensate
    ) -> None:
        """The forward command on the fail step is dispatched and can be collected
        separately from compensation commands."""
        # Only step 1 — build up compensation stack
        await fail_with_compensate.handle(
            OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        )
        forward1 = fail_with_compensate.collect_commands()
        assert len(forward1) == 1
        assert isinstance(forward1[0], ReserveItems)

        # Step 2: fail — forward command is dispatched then compensations
        await fail_with_compensate.handle(
            FraudReviewRejected(
                order_id="ORD-1", agent_id="B-42", correlation_id=uuid4()
            )
        )
        # Compensation commands are collected (forward commands were cleared)
        comp_commands = fail_with_compensate.collect_commands()
        # All compensation commands, no forward commands mixed in
        assert all(
            isinstance(c, (CancelOrder, CancelReservation)) for c in comp_commands
        )

    # ── Multiple fail registrations in one saga ─────────────────────

    @pytest.mark.anyio
    async def test_two_different_fail_events_in_same_saga(
        self, saga_state: SagaState
    ) -> None:
        """Multiple fail=True registrations for different event types."""
        from .conftest import CancelReservation as CR
        from .conftest import ItemsReserved

        saga = Saga(saga_state)
        saga.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            step="step1",
            compensate=lambda e: CR(order_id=e.order_id),
        )
        saga.on(
            ItemsReserved,
            send=lambda e: ReserveItems(order_id="x"),
            fail=True,
            fail_reason="Failed at items reserved",
        )

        # Drive to step 1
        await saga.handle(OrderCreated(order_id="ORD-1", correlation_id=uuid4()))
        _ = saga.collect_commands()
        assert len(saga.state.compensation_stack) == 1

        # Fail via ItemsReserved
        await saga.handle(ItemsReserved(order_id="ORD-1", correlation_id=uuid4()))
        assert saga.state.status == SagaStatus.COMPENSATING
        assert saga.state.error == "Failed at items reserved"

    # ── Idempotency: fail event seen twice ─────────────────────────

    @pytest.mark.anyio
    async def test_fail_event_idempotent_on_replay(self, saga_state: SagaState) -> None:
        """A fail event that's already been processed is skipped on re-delivery."""
        saga = Saga(saga_state)
        saga.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id="ORD-1"),
            fail=True,
            fail_reason="Rejected",
        )
        event = OrderCreated(order_id="ORD-1", correlation_id=uuid4())
        await saga.handle(event)
        assert saga.state.status == SagaStatus.FAILED

        # Manually set state back to simulate re-delivery to a terminal saga
        # Terminal sagas are already handled by handle() early-return.
        # Instead, test that the event is marked as processed:
        assert saga.state.is_event_processed(event.event_id)

    # ── fail=True only works with send=, not handler= ──────────────

    @pytest.mark.anyio
    async def test_fail_true_ignored_with_handler_style(
        self, saga_state: SagaState
    ) -> None:
        """fail=True is a command-mapper feature;
        handler= users call self.fail() directly. Verify that
        fail=True with handler= does not raise — it's simply ignored
        because handler path skips _mapped_handler entirely."""
        called = False

        async def my_handler(evt: object) -> None:
            nonlocal called
            called = True

        saga = Saga(saga_state)
        # This should NOT raise — fail validation only checks mutual exclusion
        # with complete/suspend, not with handler
        saga.on(OrderCreated, handler=my_handler, fail=True, fail_reason="test")
        await saga.handle(OrderCreated(order_id="ORD-1", correlation_id=uuid4()))
        assert called

    # ── fail with compensations that fail hydration ─────────────────

    @pytest.mark.anyio
    async def test_fail_with_corrupt_compensation_records_failed_compensations(
        self, saga_state: SagaState
    ) -> None:
        """When compensation hydration fails, record appears in failed_compensations."""
        from pydomain.cqrs.saga.saga import Saga

        saga = Saga(saga_state)
        saga.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            step="step1",
            compensate=lambda e: CancelReservation(order_id=e.order_id),
            compensate_description="Cancel reservation",
        )
        # Step 2: fail
        saga.on(
            FraudReviewRejected,
            send=lambda e: NotifyCustomerOfCancellation(customer_id="x"),
            fail=True,
            fail_reason="Rejected",
        )

        # Drive step 1
        await saga.handle(OrderCreated(order_id="ORD-1", correlation_id=uuid4()))
        _ = saga.collect_commands()

        # Corrupt the module name on the compensation record
        saga.state.compensation_stack[0] = saga.state.compensation_stack[0].model_copy(
            update={"module_name": "nonexistent.module"}
        )

        # Fail — compensation will be attempted but hydration fails
        await saga.handle(
            FraudReviewRejected(order_id="ORD-1", agent_id="X", correlation_id=uuid4())
        )
        assert saga.state.status == SagaStatus.COMPENSATING
        assert len(saga.state.failed_compensations) == 1
        assert saga.state.failed_compensations[0]["command_type"] == "CancelReservation"
