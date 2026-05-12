from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pydomain.ddd.id_generator import IdGenerator, Uuid7Generator


class DomainEvent(BaseModel):
    """Base class for Domain Events.

    Domain Events represent something that has happened in the domain.
    They are immutable, named in the past tense by convention, and are
    collected by aggregates during command handling.

    Tracing IDs and Immutability
    ----------------------------
    Events are frozen (``frozen=True``) and cannot be mutated after
    construction. The ``correlation_id`` and ``causation_id`` fields
    default to ``None`` because the aggregate has no access to the
    command or its tracing context — it just records facts.

    The UnitOfWork stamps these fields during
    ``commit()`` by calling :meth:`stamp`, which returns a **new copy**
    of the event via ``model_copy(update=...)``. The stamped copies
    replace the originals in the aggregate's event list. By the time
    any event handler receives the event, both IDs are populated.

    This preserves immutability while keeping the aggregate blissfully
    unaware of commands and tracing infrastructure.
    """

    _id_generator: ClassVar[IdGenerator] = Uuid7Generator()

    event_id: UUID = Field(default_factory=lambda: DomainEvent._id_generator.generate())
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    correlation_id: UUID | None = None
    causation_id: UUID | None = None

    model_config = ConfigDict(frozen=True)

    def stamp(self, *, correlation_id: UUID, causation_id: UUID) -> DomainEvent:
        """Return a new frozen copy with tracing IDs set.

        Called by the UnitOfWork during ``commit()``. The original event
        is unchanged — the stamped copy replaces it in the aggregate's
        event list.
        """
        return self.model_copy(
            update={
                "correlation_id": correlation_id,
                "causation_id": causation_id,
            }
        )

    @classmethod
    def configure(cls, *, id_generator: IdGenerator) -> None:
        """Set the system-wide ID generator for Domain Events.

        Call once at application startup. Affects all ``DomainEvent``
        subclasses that use auto-generated event IDs.
        """
        DomainEvent._id_generator = id_generator
