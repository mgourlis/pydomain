"""Tests for SagaState — mutable aggregate root tracking full saga lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from pydomain.cqrs.saga.state import (
    CompensationRecord,
    SagaState,
    SagaStatus,
    StepRecord,
)

# ═══════════════════════════════════════════════════════════════════════
# SagaStatus
# ═══════════════════════════════════════════════════════════════════════


class TestSagaStatus:
    """StrEnum with exactly 7 states."""

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
        assert isinstance(SagaStatus.PENDING, str)
        assert SagaStatus.PENDING == "PENDING"

    def test_status_count(self) -> None:
        assert len(SagaStatus) == 7

    def test_non_terminal_states(self) -> None:
        non_terminal = {
            SagaStatus.PENDING,
            SagaStatus.RUNNING,
            SagaStatus.SUSPENDED,
            SagaStatus.COMPENSATING,
        }
        for status in non_terminal:
            assert status not in {
                SagaStatus.COMPLETED,
                SagaStatus.FAILED,
                SagaStatus.COMPENSATED,
            }

    def test_terminal_states(self) -> None:
        terminal = {SagaStatus.COMPLETED, SagaStatus.FAILED, SagaStatus.COMPENSATED}
        for status in terminal:
            state = SagaState(saga_type="test", status=status)
            assert state.is_terminal is True


# ═══════════════════════════════════════════════════════════════════════
# StepRecord
# ═══════════════════════════════════════════════════════════════════════


class TestStepRecord:
    """Frozen record of a single step transition."""

    def test_frozen(self) -> None:
        rec = StepRecord(step_name="test", event_type="OrderCreated")
        with pytest.raises(Exception):
            rec.step_name = "modified"  # type: ignore[misc]

    def test_defaults(self) -> None:
        rec = StepRecord(step_name="test", event_type="OrderCreated")
        assert rec.causation_id is None
        assert rec.occurred_at is not None
        assert rec.metadata == {}

    def test_with_all_fields(self) -> None:
        eid = uuid4()
        now = datetime.now(UTC)
        rec = StepRecord(
            step_name="reserving",
            event_type="OrderCreated",
            causation_id=eid,
            occurred_at=now,
            metadata={"key": "value"},
        )
        assert rec.step_name == "reserving"
        assert rec.causation_id == eid
        assert rec.occurred_at == now
        assert rec.metadata == {"key": "value"}

    def test_equality(self) -> None:
        rec1 = StepRecord(step_name="test", event_type="OrderCreated")
        rec2 = StepRecord(step_name="test", event_type="OrderCreated")
        # Not equal because occurred_at differs (generated at different times)
        # but structural equality works if timestamps match
        assert rec1.step_name == rec2.step_name

    def test_metadata_dict(self) -> None:
        rec = StepRecord(step_name="test", event_type="E", metadata={"retry": True})
        assert rec.metadata["retry"] is True


# ═══════════════════════════════════════════════════════════════════════
# CompensationRecord
# ═══════════════════════════════════════════════════════════════════════


class TestCompensationRecord:
    """Frozen record for LIFO compensation."""

    def test_frozen(self) -> None:
        rec = CompensationRecord(command_type="CancelOrder", data={"order_id": "1"})
        with pytest.raises(Exception):
            rec.command_type = "modified"  # type: ignore[misc]

    def test_defaults(self) -> None:
        rec = CompensationRecord(command_type="CancelOrder")
        assert rec.data == {}
        assert rec.description == ""
        assert rec.module_name == ""

    def test_with_all_fields(self) -> None:
        rec = CompensationRecord(
            command_type="CancelOrder",
            data={"order_id": "ORD-1"},
            description="Cancel order",
            module_name="tests.saga.conftest",
        )
        assert rec.command_type == "CancelOrder"
        assert rec.data == {"order_id": "ORD-1"}
        assert rec.description == "Cancel order"

    def test_serialization_roundtrip(self) -> None:
        rec = CompensationRecord(
            command_type="CancelOrder",
            data={"order_id": "ORD-1", "amount": 99.99},
            description="Cancel",
            module_name="mod",
        )
        dumped = rec.model_dump()
        restored = CompensationRecord.model_validate(dumped)
        assert restored == rec


# ═══════════════════════════════════════════════════════════════════════
# SagaState — Creation & Defaults
# ═══════════════════════════════════════════════════════════════════════


class TestSagaStateCreation:
    """Default field values and initial state."""

    def test_defaults(self) -> None:
        state = SagaState(saga_type="TestSaga")
        assert state.saga_type == "TestSaga"
        assert state.status == SagaStatus.PENDING
        assert state.current_step == "init"
        assert state.step_history == []
        assert state.processed_event_ids == set()
        assert state.pending_commands == []
        assert state.compensation_stack == []
        assert state.failed_compensations == []
        assert state.error is None
        assert state.completed_at is None
        assert state.failed_at is None
        assert state.suspended_at is None
        assert state.suspension_reason is None
        assert state.timeout_at is None
        assert state.retry_count == 0
        assert state.max_retries == 3
        assert state.correlation_id is None
        assert state.causation_id is None
        assert state.metadata == {}
        assert state.created_at is not None
        assert state.updated_at is not None
        assert state.version >= 0

    def test_with_correlation_id(self) -> None:
        cid = uuid4()
        state = SagaState(saga_type="TestSaga", correlation_id=cid)
        assert state.correlation_id == cid

    def test_with_initial_status(self) -> None:
        state = SagaState(saga_type="TestSaga", status=SagaStatus.RUNNING)
        assert state.status == SagaStatus.RUNNING

    def test_auto_generates_id(self) -> None:
        state = SagaState(saga_type="TestSaga")
        assert state.id is not None

    def test_unique_ids(self) -> None:
        s1 = SagaState(saga_type="TestSaga")
        s2 = SagaState(saga_type="TestSaga")
        assert s1.id != s2.id


# ═══════════════════════════════════════════════════════════════════════
# SagaState — model_post_init (idempotency set sync)
# ═══════════════════════════════════════════════════════════════════════


class TestSagaStateModelPostInit:
    """processed_event_ids (set) handles idempotency directly."""

    def test_empty_processed_ids(self) -> None:
        state = SagaState(saga_type="TestSaga")
        assert state.is_event_processed(uuid4()) is False

    def test_prepopulated_processed_ids(self) -> None:
        eid = uuid4()
        state = SagaState(
            saga_type="TestSaga",
            processed_event_ids=[eid],
        )
        assert state.is_event_processed(eid) is True
        assert state.is_event_processed(uuid4()) is False

    def test_multiple_processed_ids(self) -> None:
        eids = [uuid4() for _ in range(5)]
        state = SagaState(
            saga_type="TestSaga",
            processed_event_ids=eids,
        )
        for eid in eids:
            assert state.is_event_processed(eid) is True


# ═══════════════════════════════════════════════════════════════════════
# SagaState — Idempotency
# ═══════════════════════════════════════════════════════════════════════


class TestSagaStateIdempotency:
    """Set-based O(1) idempotency tracking."""

    def test_mark_and_check(self) -> None:
        state = SagaState(saga_type="TestSaga")
        eid = uuid4()
        assert state.is_event_processed(eid) is False
        state.mark_event_processed(eid)
        assert state.is_event_processed(eid) is True

    def test_mark_idempotent(self) -> None:
        state = SagaState(saga_type="TestSaga")
        eid = uuid4()
        state.mark_event_processed(eid)
        state.mark_event_processed(eid)  # duplicate
        # Sets deduplicate by definition
        assert eid in state.processed_event_ids
        assert len(state.processed_event_ids) == 1

    def test_set_lookup_is_o1(self) -> None:
        state = SagaState(saga_type="TestSaga")
        eids = [uuid4() for _ in range(10)]
        for eid in eids:
            state.mark_event_processed(eid)
        assert len(state.processed_event_ids) == 10
        # Set lookup is O(1)
        for eid in eids:
            assert state.is_event_processed(eid)


# ═══════════════════════════════════════════════════════════════════════
# SagaState — Step Tracking
# ═══════════════════════════════════════════════════════════════════════


class TestSagaStateStepTracking:
    """record_step() and step_history management."""

    def test_record_step_appends(self) -> None:
        state = SagaState(saga_type="TestSaga")
        state.record_step("reserving", "OrderCreated")
        assert len(state.step_history) == 1
        assert state.step_history[0].step_name == "reserving"
        assert state.step_history[0].event_type == "OrderCreated"

    def test_record_step_updates_current_step(self) -> None:
        state = SagaState(saga_type="TestSaga")
        assert state.current_step == "init"
        state.record_step("reserving", "OrderCreated")
        assert state.current_step == "reserving"

    def test_multiple_steps(self) -> None:
        state = SagaState(saga_type="TestSaga")
        state.record_step("reserving", "OrderCreated")
        state.record_step("confirming", "ItemsReserved")
        assert len(state.step_history) == 2
        assert state.current_step == "confirming"

    def test_record_step_with_causation_id(self) -> None:
        state = SagaState(saga_type="TestSaga")
        cid = uuid4()
        state.record_step("reserving", "OrderCreated", causation_id=cid)
        assert state.step_history[0].causation_id == cid

    def test_record_step_with_metadata(self) -> None:
        state = SagaState(saga_type="TestSaga")
        state.record_step("reserving", "OrderCreated", metadata={"key": "val"})
        assert state.step_history[0].metadata == {"key": "val"}

    def test_record_step_touches(self) -> None:
        state = SagaState(saga_type="TestSaga")
        before = state.updated_at
        state.record_step("reserving", "OrderCreated")
        assert state.updated_at >= before


# ═══════════════════════════════════════════════════════════════════════
# SagaState — Terminal State
# ═══════════════════════════════════════════════════════════════════════


class TestSagaStateTerminal:
    """is_terminal property for COMPLETED, FAILED, COMPENSATED."""

    @pytest.mark.parametrize(
        "status",
        [
            SagaStatus.COMPLETED,
            SagaStatus.FAILED,
            SagaStatus.COMPENSATED,
        ],
    )
    def test_terminal_states(self, status: SagaStatus) -> None:
        state = SagaState(saga_type="TestSaga", status=status)
        assert state.is_terminal is True

    @pytest.mark.parametrize(
        "status",
        [
            SagaStatus.PENDING,
            SagaStatus.RUNNING,
            SagaStatus.SUSPENDED,
            SagaStatus.COMPENSATING,
        ],
    )
    def test_non_terminal_states(self, status: SagaStatus) -> None:
        state = SagaState(saga_type="TestSaga", status=status)
        assert state.is_terminal is False


# ═══════════════════════════════════════════════════════════════════════
# SagaState — Touch
# ═══════════════════════════════════════════════════════════════════════


class TestSagaStateTouch:
    """touch() updates updated_at and increments version."""

    def test_touch_updates_timestamp(self) -> None:
        state = SagaState(saga_type="TestSaga")
        before = state.updated_at
        state.touch()
        assert state.updated_at >= before

    def test_touch_increments_version(self) -> None:
        state = SagaState(saga_type="TestSaga")
        v_before = state.version
        state.touch()
        assert state.version == v_before + 1

    def test_multiple_touches(self) -> None:
        state = SagaState(saga_type="TestSaga")
        v0 = state.version
        state.touch()
        state.touch()
        state.touch()
        assert state.version == v0 + 3


# ═══════════════════════════════════════════════════════════════════════
# SagaState — Metadata
# ═══════════════════════════════════════════════════════════════════════


class TestSagaStateMetadata:
    """Arbitrary context via metadata dict."""

    def test_default_metadata(self) -> None:
        state = SagaState(saga_type="TestSaga")
        assert state.metadata == {}

    def test_set_metadata(self) -> None:
        state = SagaState(
            saga_type="TestSaga",
            metadata={"source": "api", "priority": "high"},
        )
        assert state.metadata["source"] == "api"
        assert state.metadata["priority"] == "high"

    def test_mutate_metadata(self) -> None:
        state = SagaState(saga_type="TestSaga")
        state.metadata["retry"] = True
        assert state.metadata["retry"] is True


# ═══════════════════════════════════════════════════════════════════════
# SagaState — Distributed Tracing
# ═══════════════════════════════════════════════════════════════════════


class TestSagaStateTracing:
    """correlation_id and causation_id fields."""

    def test_correlation_id_none_by_default(self) -> None:
        state = SagaState(saga_type="TestSaga")
        assert state.correlation_id is None

    def test_causation_id_none_by_default(self) -> None:
        state = SagaState(saga_type="TestSaga")
        assert state.causation_id is None

    def test_set_correlation_id(self) -> None:
        cid = uuid4()
        state = SagaState(saga_type="TestSaga", correlation_id=cid)
        assert state.correlation_id == cid

    def test_set_causation_id(self) -> None:
        caus = uuid4()
        state = SagaState(saga_type="TestSaga", causation_id=caus)
        assert state.causation_id == caus


# ═══════════════════════════════════════════════════════════════════════
# SagaState — Suspension Fields
# ═══════════════════════════════════════════════════════════════════════


class TestSagaStateSuspension:
    """suspended_at, suspension_reason, timeout_at."""

    def test_defaults(self) -> None:
        state = SagaState(saga_type="TestSaga")
        assert state.suspended_at is None
        assert state.suspension_reason is None
        assert state.timeout_at is None

    def test_set_suspended(self) -> None:
        now = datetime.now(UTC)
        state = SagaState(
            saga_type="TestSaga",
            status=SagaStatus.SUSPENDED,
            suspended_at=now,
            suspension_reason="Waiting for approval",
            timeout_at=now + timedelta(hours=1),
        )
        assert state.suspended_at == now
        assert state.suspension_reason == "Waiting for approval"
        assert state.timeout_at is not None


# ═══════════════════════════════════════════════════════════════════════
# SagaState — Pending Commands
# ═══════════════════════════════════════════════════════════════════════


class TestSagaStatePendingCommands:
    """pending_commands list for crash recovery."""

    def test_default_empty(self) -> None:
        state = SagaState(saga_type="TestSaga")
        assert state.pending_commands == []

    def test_with_pending_commands(self) -> None:
        state = SagaState(
            saga_type="TestSaga",
            pending_commands=[
                {
                    "command_type": "ReserveItems",
                    "data": {"order_id": "1"},
                    "dispatched": False,
                },
            ],
        )
        assert len(state.pending_commands) == 1
        assert state.pending_commands[0]["command_type"] == "ReserveItems"


# ═══════════════════════════════════════════════════════════════════════
# SagaState — Retry Fields
# ═══════════════════════════════════════════════════════════════════════


class TestSagaStateRetries:
    """retry_count and max_retries."""

    def test_defaults(self) -> None:
        state = SagaState(saga_type="TestSaga")
        assert state.retry_count == 0
        assert state.max_retries == 3

    def test_custom_max_retries(self) -> None:
        state = SagaState(saga_type="TestSaga", max_retries=5)
        assert state.max_retries == 5

    def test_retry_count_increment(self) -> None:
        state = SagaState(saga_type="TestSaga")
        state.retry_count = 2
        assert state.retry_count == 2


# ═══════════════════════════════════════════════════════════════════════
# SagaState — Compensation Stack
# ═══════════════════════════════════════════════════════════════════════


class TestSagaStateCompensationStack:
    """compensation_stack and failed_compensations."""

    def test_default_empty_stack(self) -> None:
        state = SagaState(saga_type="TestSaga")
        assert state.compensation_stack == []
        assert state.failed_compensations == []

    def test_with_compensation_records(self) -> None:
        rec = CompensationRecord(command_type="CancelOrder", data={"order_id": "1"})
        state = SagaState(
            saga_type="TestSaga",
            compensation_stack=[rec],
        )
        assert len(state.compensation_stack) == 1

    def test_with_failed_compensations(self) -> None:
        state = SagaState(
            saga_type="TestSaga",
            failed_compensations=[
                {"command_type": "CancelOrder", "error": "service down"},
            ],
        )
        assert len(state.failed_compensations) == 1


# ═══════════════════════════════════════════════════════════════════════
# SagaState — Audit Timestamps
# ═══════════════════════════════════════════════════════════════════════


class TestSagaStateTimestamps:
    """created_at, updated_at, completed_at, failed_at."""

    def test_created_at_set_on_init(self) -> None:
        state = SagaState(saga_type="TestSaga")
        assert state.created_at is not None
        assert isinstance(state.created_at, datetime)

    def test_updated_at_set_on_init(self) -> None:
        state = SagaState(saga_type="TestSaga")
        assert state.updated_at is not None

    def test_completed_at_none_by_default(self) -> None:
        state = SagaState(saga_type="TestSaga")
        assert state.completed_at is None

    def test_failed_at_none_by_default(self) -> None:
        state = SagaState(saga_type="TestSaga")
        assert state.failed_at is None

    def test_set_completed_at(self) -> None:
        now = datetime.now(UTC)
        state = SagaState(saga_type="TestSaga", completed_at=now)
        assert state.completed_at == now

    def test_set_failed_at(self) -> None:
        now = datetime.now(UTC)
        state = SagaState(saga_type="TestSaga", failed_at=now)
        assert state.failed_at == now


# ═══════════════════════════════════════════════════════════════════════
# SagaState — Error Tracking
# ═══════════════════════════════════════════════════════════════════════


class TestSagaStateError:
    """error field for failure messages."""

    def test_error_none_by_default(self) -> None:
        state = SagaState(saga_type="TestSaga")
        assert state.error is None

    def test_set_error(self) -> None:
        state = SagaState(saga_type="TestSaga", error="Payment failed")
        assert state.error == "Payment failed"


# ═══════════════════════════════════════════════════════════════════════
# SagaState — Serialization
# ═══════════════════════════════════════════════════════════════════════


class TestSagaStateSerialization:
    """model_dump() and model_validate() round-trips."""

    def test_roundtrip(self) -> None:
        cid = uuid4()
        caus = uuid4()
        state = SagaState(
            saga_type="TestSaga",
            correlation_id=cid,
            causation_id=caus,
            status=SagaStatus.RUNNING,
            current_step="reserving",
            metadata={"key": "val"},
        )
        dumped = state.model_dump()
        restored = SagaState.model_validate(dumped)
        assert restored.saga_type == "TestSaga"
        assert restored.correlation_id == cid
        assert restored.causation_id == caus
        assert restored.status == SagaStatus.RUNNING
        assert restored.current_step == "reserving"

    def test_with_step_history(self) -> None:
        state = SagaState(saga_type="TestSaga")
        state.record_step("reserving", "OrderCreated")
        state.record_step("confirming", "ItemsReserved")
        dumped = state.model_dump()
        restored = SagaState.model_validate(dumped)
        assert len(restored.step_history) == 2

    def test_with_processed_events(self) -> None:
        eids = [uuid4() for _ in range(3)]
        state = SagaState(saga_type="TestSaga", processed_event_ids=eids)
        dumped = state.model_dump()
        restored = SagaState.model_validate(dumped)
        assert restored.processed_event_ids == set(eids)
        for eid in eids:
            assert restored.is_event_processed(eid)

    def test_processed_event_ids_serialized_as_list(self) -> None:
        """model_dump() produces a list for JSON/DB compatibility."""
        eids = [uuid4() for _ in range(3)]
        state = SagaState(saga_type="TestSaga", processed_event_ids=eids)
        dumped = state.model_dump()
        # Internally a set, but serialised as list
        assert isinstance(dumped["processed_event_ids"], list)
        assert set(dumped["processed_event_ids"]) == set(eids)

    def test_processed_event_ids_accepts_list_from_db(self) -> None:
        """model_validate() accepts list[UUID] and converts to set[UUID]."""
        eids = [uuid4() for _ in range(3)]
        data = {"saga_type": "TestSaga", "processed_event_ids": eids}
        state = SagaState.model_validate(data)
        assert isinstance(state.processed_event_ids, set)
        assert state.processed_event_ids == set(eids)

    def test_processed_event_ids_accepts_set(self) -> None:
        """model_validate() also accepts set[UUID] directly."""
        eids = {uuid4() for _ in range(3)}
        data = {"saga_type": "TestSaga", "processed_event_ids": eids}
        state = SagaState.model_validate(data)
        assert isinstance(state.processed_event_ids, set)
        assert state.processed_event_ids == eids


# ═══════════════════════════════════════════════════════════════════════
# SagaState — Memory Bounds
# ═══════════════════════════════════════════════════════════════════════


class TestSagaStateMemoryBounds:
    """max_processed_events and max_step_history caps."""

    def test_max_processed_events_caps_set_on_mark(self) -> None:
        original = SagaState.max_processed_events
        SagaState.max_processed_events = 3  # type: ignore[attr-defined]
        try:
            state = SagaState(saga_type="TestSaga")
            eids = [uuid4() for _ in range(5)]
            for eid in eids:
                state.mark_event_processed(eid)
            assert len(state.processed_event_ids) == 3
        finally:
            SagaState.max_processed_events = original  # type: ignore[attr-defined]

    def test_max_step_history_caps_on_record(self) -> None:
        original = SagaState.max_step_history
        SagaState.max_step_history = 2  # type: ignore[attr-defined]
        try:
            state = SagaState(saga_type="TestSaga")
            for i in range(5):
                state.record_step(f"step{i}", f"Event{i}")
            assert len(state.step_history) == 2
            # Most recent steps kept
            assert state.step_history[0].step_name == "step3"
            assert state.step_history[1].step_name == "step4"
        finally:
            SagaState.max_step_history = original  # type: ignore[attr-defined]

    def test_zero_means_unlimited(self) -> None:
        """Default (0) means no automatic pruning."""
        state = SagaState(saga_type="TestSaga")
        for i in range(20):
            state.mark_event_processed(uuid4())
            state.record_step(f"step{i}", f"Event{i}")
        assert len(state.processed_event_ids) == 20
        assert len(state.step_history) == 20


# ═══════════════════════════════════════════════════════════════════════
# SagaState — Explicit Pruning
# ═══════════════════════════════════════════════════════════════════════


class TestSagaStatePruneHistory:
    """prune_history() for explicit, custom-schedule trimming."""

    def test_prune_step_history(self) -> None:
        state = SagaState(saga_type="TestSaga")
        for i in range(10):
            state.record_step(f"step{i}", f"Event{i}")
        state.prune_history(keep_last_n_steps=3)
        assert len(state.step_history) == 3
        assert state.step_history[-1].step_name == "step9"

    def test_prune_processed_events(self) -> None:
        state = SagaState(saga_type="TestSaga")
        eids = [uuid4() for _ in range(10)]
        for eid in eids:
            state.mark_event_processed(eid)
        state.prune_history(keep_last_n_events=5)
        assert len(state.processed_event_ids) == 5

    def test_prune_both(self) -> None:
        state = SagaState(saga_type="TestSaga")
        for i in range(10):
            state.mark_event_processed(uuid4())
            state.record_step(f"step{i}", f"Event{i}")
        state.prune_history(keep_last_n_steps=2, keep_last_n_events=3)
        assert len(state.step_history) == 2
        assert len(state.processed_event_ids) == 3

    def test_prune_step_history_to_zero(self) -> None:
        state = SagaState(saga_type="TestSaga")
        state.record_step("step0", "Event0")
        state.prune_history(keep_last_n_steps=0)
        assert state.step_history == []

    def test_prune_events_to_zero(self) -> None:
        state = SagaState(saga_type="TestSaga")
        state.mark_event_processed(uuid4())
        state.prune_history(keep_last_n_events=0)
        assert state.processed_event_ids == set()

    def test_prune_none_means_no_change(self) -> None:
        state = SagaState(saga_type="TestSaga")
        state.record_step("step0", "Event0")
        state.mark_event_processed(uuid4())
        state.prune_history()  # None for both — no pruning
        assert len(state.step_history) == 1
        assert len(state.processed_event_ids) == 1
