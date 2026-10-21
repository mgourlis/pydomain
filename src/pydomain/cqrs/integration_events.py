from __future__ import annotations

import math
from datetime import UTC, datetime
from functools import cache
from typing import TYPE_CHECKING, Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
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

_PRIMITIVES_RULE = (
    "IntegrationEvent fields must be primitives only: str, int, finite float, "
    "bool, None, and dict (with str keys) or list holding them."
)


def _primitive_problem(value: object, path: str) -> str | None:
    """Describe the first non-JSON value in *value*, or ``None`` if it is clean.

    The description names the value's type and its *path* (field name, dict
    keys, list indexes), never the value itself (ADR-071, ADR-074).
    """
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                key_type = type(key).__name__
                return f"has a dict key of disallowed type '{key_type}' at {path}"
            problem = _primitive_problem(item, f"{path}[{key!r}]")
            if problem is not None:
                return problem
        return None
    if isinstance(value, list):
        for index, item in enumerate(value):
            problem = _primitive_problem(item, f"{path}[{index}]")
            if problem is not None:
                return problem
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return f"has a non-finite float at {path}"
    if not isinstance(value, _ALLOWED_PRIMITIVES):
        return f"has disallowed type '{type(value).__name__}' at {path}"
    return None


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
        """Validate that every field value is strict JSON, at any depth.

        Integration events must only carry primitive types to ensure they
        can be serialized by message brokers without custom serialization
        logic. The check runs through dict values and list items; tuples,
        sets, non-``str`` dict keys, NaN and infinity are refused
        (ADR-074).
        """
        for name in type(self).model_fields:
            problem = _primitive_problem(getattr(self, name), name)
            if problem is not None:
                raise ValueError(f"Field '{name}' {problem}. {_PRIMITIVES_RULE}")
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

        Only the two ids are validated, each against its own field's type:
        a value that is not a string raises
        :class:`pydantic.ValidationError`. No model validator runs
        (ADR-074, as ``DomainEvent.stamp()`` in ADR-070).
        """
        return self.model_copy(
            update={
                "correlation_id": _id_adapter(
                    type(self), "correlation_id"
                ).validate_python(correlation_id),
                "causation_id": _id_adapter(type(self), "causation_id").validate_python(
                    causation_id
                ),
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


@cache
def _id_adapter(event_type: type[IntegrationEvent], name: str) -> TypeAdapter[Any]:
    """The validator of one tracing-id field of *event_type*, built once."""
    field = event_type.model_fields[name]
    annotated: Any = Annotated[field.annotation, field]
    return TypeAdapter(annotated)
