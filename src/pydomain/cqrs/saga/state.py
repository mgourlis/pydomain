"""Saga state model — mutable aggregate root tracking full saga lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from pydomain.ddd.aggregate_root import AggregateRoot


class SagaStatus(StrEnum):
    """Possible lifecycle states for a saga instance."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUSPENDED = "SUSPENDED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    COMPENSATING = "COMPENSATING"
    COMPENSATED = "COMPENSATED"


class StepRecord(BaseModel):
    """Immutable record of a single saga step transition."""

    model_config = ConfigDict(frozen=True)

    step_name: str
    event_type: str
    causation_id: UUID | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompensationRecord(BaseModel):
    """Serialised compensating command for LIFO execution on failure."""

    model_config = ConfigDict(frozen=True)

    command_type: str
    data: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    module_name: str = ""


class SagaState(AggregateRoot[UUID]):
    """Mutable aggregate root that tracks the full lifecycle of a saga instance.

    Covers identity, lifecycle, step history, idempotency, pending commands,
    compensation stack, suspension, timeouts, retries, audit timestamps,
    and optimistic concurrency.

    Memory bounds:
        * ``max_processed_events`` caps ``processed_event_ids`` (set to ``0`` to
          store all events; default ``0`` for backward compatibility).
        * ``max_step_history`` caps ``step_history`` (set to ``0`` to store all
          steps; default ``0`` for backward compatibility).
        * Call :meth:`prune_history` explicitly for custom pruning schedules.
    """

    # ── Memory-bounds config (ClassVar — not serialised) ───────────
    # Set to 0 for unlimited (backward-compatible default).
    # Override at the class level in subclasses, e.g.:
    #   class MyState(SagaState):
    #       max_processed_events = 100
    max_processed_events: ClassVar[int] = 0
    max_step_history: ClassVar[int] = 0

    # ── Identity ────────────────────────────────────────────────────
    saga_type: str = ""
    status: SagaStatus = SagaStatus.PENDING

    # ── Step Tracking ────────────────────────────────────────────────
    current_step: str = "init"
    step_history: list[StepRecord] = Field(default_factory=list[StepRecord])

    # ── Idempotency ─────────────────────────────────────────────────
    # Stored as a set internally (single copy, O(1) lookup).
    # Serialised as a list for JSON/DB compatibility.
    processed_event_ids: set[UUID] = Field(default_factory=set[UUID])

    # ── Pending Commands ─────────────────────────────────────────────
    pending_commands: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])

    # ── Compensation ─────────────────────────────────────────────────
    # NOTE: compensation_stack grows with each forward step that
    # registers a compensation.  It is cleared on compensation
    # execution, but for sagas with very many steps the in-memory
    # footprint can become significant.  Consider capping or
    # streaming compensations for extremely long-lived sagas.
    compensation_stack: list[CompensationRecord] = Field(
        default_factory=list[CompensationRecord]
    )
    failed_compensations: list[dict[str, Any]] = Field(
        default_factory=list[dict[str, Any]]
    )

    # ── Suspension / Human-in-the-Loop ──────────────────────────────
    suspended_at: datetime | None = None
    suspension_reason: str | None = None
    timeout_at: datetime | None = None

    # ── Retries ──────────────────────────────────────────────────────
    retry_count: int = 0
    max_retries: int = 3

    # ── Error Tracking ──────────────────────────────────────────────
    error: str | None = None

    # ── Completion timestamps ────────────────────────────────────────
    completed_at: datetime | None = None
    failed_at: datetime | None = None

    # ── Distributed Tracing ──────────────────────────────────────────
    correlation_id: UUID | None = None
    causation_id: UUID | None = None

    # ── Audit (inline — no AuditableMixin in pydomain) ──────────────
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # ── Arbitrary Context ────────────────────────────────────────────
    metadata: dict[str, Any] = Field(default_factory=dict)

    # ── Serialisation helpers ────────────────────────────────────────

    @field_serializer("processed_event_ids", mode="plain")
    @classmethod
    def _serialize_event_ids_as_list(cls, v: set[UUID]) -> list[UUID]:
        """Serialise ``processed_event_ids`` as a list for JSON/DB storage."""
        return list(v)

    @field_validator("processed_event_ids", mode="before")
    @classmethod
    def _coerce_event_ids_from_list(cls, v: object) -> set[UUID]:
        """Accept ``list[UUID]`` from persistence and convert to ``set[UUID]``."""
        if isinstance(v, list):
            return set(v)  # pyright: ignore[reportUnknownArgumentType]
        if isinstance(v, set):
            return v  # pyright: ignore[reportUnknownVariableType]
        return set()

    # ── Helpers ──────────────────────────────────────────────────────

    def is_event_processed(self, event_id: UUID) -> bool:
        """Return ``True`` if the event has already been handled (O(1))."""
        return event_id in self.processed_event_ids

    def mark_event_processed(self, event_id: UUID) -> None:
        """Record an event id to prevent duplicate processing."""
        self.processed_event_ids.add(event_id)
        self._enforce_max_processed_events()

    def record_step(
        self,
        step_name: str,
        event_type: str,
        *,
        causation_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append a step record and update ``current_step``."""
        self.current_step = step_name
        self.step_history.append(
            StepRecord(
                step_name=step_name,
                event_type=event_type,
                causation_id=causation_id,
                metadata=metadata or {},
            )
        )
        self._enforce_max_step_history()
        self.touch()

    @property
    def is_terminal(self) -> bool:
        """Return ``True`` if the saga has reached a final state."""
        return self.status in (
            SagaStatus.COMPLETED,
            SagaStatus.FAILED,
            SagaStatus.COMPENSATED,
        )

    def record_failed_compensation(
        self,
        command_type: str,
        data: dict[str, Any],
        module_name: str,
        error: str,
    ) -> None:
        """Append a failed-compensation record for audit."""
        self.failed_compensations.append(
            {
                "command_type": command_type,
                "data": data,
                "module_name": module_name,
                "error": error,
            }
        )

    def touch(self) -> None:
        """Update ``updated_at`` to the current time and bump version."""
        self.updated_at = datetime.now(UTC)
        self.version += 1

    # ── Memory-bound enforcement ────────────────────────────────────

    def _enforce_max_processed_events(self) -> None:
        """Trim ``processed_event_ids`` to ``max_processed_events`` if set."""
        limit = self.max_processed_events
        if limit > 0 and len(self.processed_event_ids) > limit:
            # Discard oldest entries — keep the most recent *limit* ids.
            # Sets are unordered, but the only guarantee we need is the
            # cardinality cap; exact eviction order is not observable.
            self.processed_event_ids = set(list(self.processed_event_ids)[-limit:])

    def _enforce_max_step_history(self) -> None:
        """Trim ``step_history`` to ``max_step_history`` if set."""
        limit = self.max_step_history
        if limit > 0 and len(self.step_history) > limit:
            self.step_history = self.step_history[-limit:]

    def prune_history(
        self,
        *,
        keep_last_n_steps: int | None = None,
        keep_last_n_events: int | None = None,
    ) -> None:
        """Explicitly trim unbounded collections.

        Useful for long-lived sagas that need periodic garbage collection
        independent of the automatic ``max_*`` caps.

        Args:
            keep_last_n_steps: Keep at most this many recent ``StepRecord``
                entries.  ``None`` means don't prune step history.
            keep_last_n_events: Keep at most this many recent event IDs.
                ``None`` means don't prune processed events.
        """
        if keep_last_n_steps is not None and keep_last_n_steps >= 0:
            self.step_history = (
                self.step_history[-keep_last_n_steps:] if keep_last_n_steps > 0 else []
            )
        if keep_last_n_events is not None and keep_last_n_events >= 0:
            self.processed_event_ids = (
                set(list(self.processed_event_ids)[-keep_last_n_events:])
                if keep_last_n_events > 0
                else set()
            )
        self.touch()
