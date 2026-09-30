from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from uuid_utils import uuid7

if TYPE_CHECKING:
    from pydomain.ddd.domain_event import DomainEvent

_ALLOWED_PRIMITIVES: tuple[type, ...] = (
    str,
    int,
    float,
    bool,
    dict,
    list,
    type(None),
)


class IntegrationEvent(BaseModel):
    """Base class for Integration Events.

    Integration Events are the cross-boundary counterpart to Domain Events.
    They carry primitives only (str, int, float, bool, dict, list) and are
    published to external consumers via a ``MessageBroker``.

    Tracing IDs and Immutability
    ----------------------------
    Events are frozen (``frozen=True``) and cannot be mutated after
    construction. The ``correlation_id`` and ``causation_id`` fields
    default to ``None`` because domain code has no access to tracing
    context when creating the event — it just records facts.

    The publisher stamps these fields before calling
    ``MessageBroker.publish()`` by calling :meth:`stamp`, which returns a
    **new copy** of the event with tracing IDs set.  This mirrors the
    ``DomainEvent.stamp()`` pattern.

    Fields are immutable (frozen=True). ``event_id`` and ``occurred_at`` are
    auto-generated as primitive strings to satisfy broker serialization
    requirements.
    """

    event_id: str = Field(default_factory=lambda: str(uuid7()))
    occurred_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    event_version: int = 1
    correlation_id: str | None = None
    causation_id: str | None = None

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="after")
    def _validate_primitive_fields(self) -> IntegrationEvent:
        """Validate that all field values are primitive types.

        Integration events must only carry primitive types to ensure they
        can be serialized by message brokers without custom serialization
        logic.
        """
        for name in type(self).model_fields:
            value = getattr(self, name)
            if not isinstance(value, _ALLOWED_PRIMITIVES):
                raise ValueError(
                    f"Field '{name}' has disallowed type '{type(value).__name__}'. "
                    "IntegrationEvent fields must be primitives only: "
                    "str, int, float, bool, dict, list, None."
                )
        return self

    def stamp(
        self,
        *,
        correlation_id: str,
        causation_id: str,
    ) -> Self:
        """Return a new frozen copy with tracing IDs set.

        Called by the publisher before ``MessageBroker.publish()``.
        The original event is unchanged — the stamped copy replaces it.

        Parameters
        ----------
        correlation_id:
            The business process identifier (UUID7 as string).  Stays
            constant across every event in the same workflow.
        causation_id:
            The ID of the event or command that directly caused this one
            (UUID7 as string).
        """
        return self.model_copy(
            update={
                "correlation_id": correlation_id,
                "causation_id": causation_id,
            }
        )

    def stamp_from(self, domain_event: DomainEvent) -> Self:
        """Return a new frozen copy with tracing IDs from a DomainEvent.

        Convenience wrapper around :meth:`stamp` — extracts
        ``correlation_id`` and ``event_id`` from *domain_event*.

        Parameters
        ----------
        domain_event:
            A DomainEvent that has been stamped by the UnitOfWork.

        Returns
        -------
        Self
            A new frozen copy with tracing IDs set.

        Raises
        ------
        ValueError
            If *domain_event* has ``None`` correlation_id (not yet stamped).
        """
        if domain_event.correlation_id is None:
            raise ValueError(
                "Cannot stamp from DomainEvent with None correlation_id. "
                "Ensure the event has been stamped by the UnitOfWork."
            )
        return self.stamp(
            correlation_id=str(domain_event.correlation_id),
            causation_id=str(domain_event.event_id),
        )
