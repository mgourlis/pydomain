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

    The ``correlation_id`` and ``causation_id`` fields are set by the
    infrastructure layer (UnitOfWork) during ``commit()``, not by the
    aggregate. Both default to ``None`` until stamped.
    """

    _id_generator: ClassVar[IdGenerator] = Uuid7Generator()

    event_id: UUID = Field(default_factory=lambda: DomainEvent._id_generator.generate())
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    correlation_id: UUID | None = None
    causation_id: UUID | None = None

    model_config = ConfigDict(frozen=True)

    @classmethod
    def configure(cls, *, id_generator: IdGenerator) -> None:
        """Set the system-wide ID generator for Domain Events.

        Call once at application startup. Affects all ``DomainEvent``
        subclasses that use auto-generated event IDs.
        """
        DomainEvent._id_generator = id_generator
