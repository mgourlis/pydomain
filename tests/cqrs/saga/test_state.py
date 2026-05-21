"""Tests for SagaState — the saga aggregate root."""

from __future__ import annotations

from uuid import uuid4

import pytest

from pydomain.cqrs.saga.state import (
    CompensationRecord,
    SagaState,
    SagaStatus,
    StepRecord,
)


class TestSagaStatus:
    """SagaStatus StrEnum values."""

    def test_all_status_values(self) -> None:
        expected = {
            "PENDING",
            "RUNNING",
            "SUSPENDED",
            "COMPLETED",
            "FAILED",
            "COMPENSATING",
            "COMPENSATED",
        }
        actual = {s.value for s in SagaStatus}
        assert actual == expected

    def test_status_is_string(self) -> None:
        assert isinstance(SagaStatus.RUNNING, str)
        assert SagaStatus.RUNNING == "RUNNING"


class TestStepRecord:
    """StepRecord frozen model."""

    def test_create_step_record(self) -> None:
        record = StepRecord(step_name="reserving", event_type="OrderCreated")
        assert record.step_name == "reserving"
        assert record.event_type == "OrderCreated"
        assert record.occurred_at is not None
        assert record.metadata == {}

    def test_step_record_is_frozen(self) -> None:
        record = StepRecord(step_name="step1", event_type="Evt")
        with pytest.raises(Exception):
            record.step_name = "changed"  # type: ignore[misc]


class TestCompensationRecord:
    """CompensationRecord frozen model."""

    def test_create_compensation_record(self) -> None:
        record = CompensationRecord(
            command_type="CancelOrder",
            data={"order_id": "abc"},
            description="Cancel the order",
        )
        assert record.command_type == "CancelOrder"
        assert record.data == {"order_id": "abc"}
        assert record.description == "Cancel the order"

    def test_compensation_record_defaults(self) -> None:
        record = CompensationRecord(command_type="CancelOrder")
        assert record.data == {}
        assert record.description == ""


class TestSagaStateCreation:
    """SagaState construction and defaults."""

    def test_default_state(self) -> None:
        state = SagaState(saga_type="TestSaga")
        assert state.saga_type == "TestSaga"
        assert state.status == SagaStatus.PENDING
        assert state.current_step == "init"
        assert state.step_history == []
        assert state.processed_event_ids == set()
        assert state.pending_commands == []
        assert state.compensation_stack == []
        assert state.failed_compensations == []
        assert state.suspended_at is None
        assert state.suspension_reason is None
        assert state.timeout_at is None
        assert state.retry_count == 0
        assert state.max_retries == 3
        assert state.error is None
        assert state.completed_at is None
        assert state.failed_at is None
        assert state.correlation_id is None
        assert state.created_at is not None
        assert state.updated_at is not None
        assert state.metadata == {}

    def test_state_with_correlation_id(self) -> None:
        cid = uuid4()
        state = SagaState(saga_type="OrderSaga", correlation_id=cid)
        assert state.correlation_id == cid

    def test_state_has_uuid_id(self) -> None:
        state = SagaState(saga_type="TestSaga")
        assert isinstance(state.id, type(uuid4()))


class TestIdempotency:
    """Event dedup via processed_event_ids."""

    def test_event_not_processed_initially(self) -> None:
        state = SagaState(saga_type="TestSaga")
        event_id = uuid4()
        assert state.is_event_processed(event_id) is False

    def test_mark_event_processed(self) -> None:
        state = SagaState(saga_type="TestSaga")
        event_id = uuid4()
        state.mark_event_processed(event_id)
        assert state.is_event_processed(event_id) is True
        assert event_id in state.processed_event_ids

    def test_duplicate_mark_is_idempotent(self) -> None:
        state = SagaState(saga_type="TestSaga")
        event_id = uuid4()
        state.mark_event_processed(event_id)
        state.mark_event_processed(event_id)
        # Set deduplicates by definition
        assert event_id in state.processed_event_ids

    def test_multiple_events(self) -> None:
        state = SagaState(saga_type="TestSaga")
        e1, e2, e3 = uuid4(), uuid4(), uuid4()
        state.mark_event_processed(e1)
        state.mark_event_processed(e2)
        state.mark_event_processed(e3)
        assert state.is_event_processed(e1)
        assert state.is_event_processed(e2)
        assert state.is_event_processed(e3)
        assert len(state.processed_event_ids) == 3


class TestStepTracking:
    """record_step() and step_history."""

    def test_record_step(self) -> None:
        state = SagaState(saga_type="TestSaga")
        state.record_step("reserving", "OrderCreated")
        assert state.current_step == "reserving"
        assert len(state.step_history) == 1
        assert state.step_history[0].step_name == "reserving"
        assert state.step_history[0].event_type == "OrderCreated"

    def test_record_multiple_steps(self) -> None:
        state = SagaState(saga_type="TestSaga")
        state.record_step("reserving", "OrderCreated")
        state.record_step("confirming", "ItemsReserved")
        assert state.current_step == "confirming"
        assert len(state.step_history) == 2

    def test_record_step_with_metadata(self) -> None:
        state = SagaState(saga_type="TestSaga")
        state.record_step("reserving", "OrderCreated", metadata={"order_total": 100})
        assert state.step_history[0].metadata == {"order_total": 100}

    def test_record_step_updates_updated_at(self) -> None:
        state = SagaState(saga_type="TestSaga")
        before = state.updated_at
        # Ensure some time passes (not strictly needed but clearer)
        state.record_step("step1", "Evt")
        # updated_at should be >= before (may be equal in fast tests)
        assert state.updated_at >= before


class TestTerminalState:
    """is_terminal property."""

    @pytest.mark.parametrize(
        "status, expected",
        [
            (SagaStatus.PENDING, False),
            (SagaStatus.RUNNING, False),
            (SagaStatus.SUSPENDED, False),
            (SagaStatus.COMPENSATING, False),
            (SagaStatus.COMPLETED, True),
            (SagaStatus.FAILED, True),
            (SagaStatus.COMPENSATED, True),
        ],
    )
    def test_terminal_states(self, status: SagaStatus, expected: bool) -> None:
        state = SagaState(saga_type="TestSaga", status=status)
        assert state.is_terminal is expected


class TestTouch:
    """touch() updates updated_at."""

    def test_touch_updates_timestamp(self) -> None:
        state = SagaState(saga_type="TestSaga")
        before = state.updated_at
        state.touch()
        assert state.updated_at >= before
