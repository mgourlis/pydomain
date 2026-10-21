from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, ClassVar, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

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
    ``commit()`` by calling :meth:`stamp`, which returns a **new deep
    copy** of the event. The stamped copies replace the originals in the
    aggregate's event list. By the time any event handler receives the
    event, both IDs are populated.

    This preserves immutability while keeping the aggregate blissfully
    unaware of commands and tracing infrastructure.

    Event IDs
    ---------
    When the model is validated, a missing ``event_id`` comes from
    ``cls._id_generator``: a subclass that sets its own ``_id_generator``
    class variable uses it, every other subclass uses the one set by
    :meth:`configure` (ADR-070). ``model_construct()`` skips validation,
    so its ``event_id`` comes from the base class's generator.
    """

    _id_generator: ClassVar[IdGenerator[UUID]] = Uuid7Generator()

    event_id: UUID = Field(default_factory=lambda: DomainEvent._id_generator.generate())
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_version: int = 1
    correlation_id: UUID | None = None
    causation_id: UUID | None = None

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="before")
    @classmethod
    def _ensure_event_id(cls, data: Any) -> Any:
        if isinstance(data, dict) and "event_id" not in data:
            return {**data, "event_id": cls._id_generator.generate()}  # pyright: ignore[reportUnknownVariableType]
        return data

    def stamp(self, *, correlation_id: UUID, causation_id: UUID) -> Self:
        """Return a new frozen copy with tracing IDs set.

        Called by the UnitOfWork during ``commit()``. The original event
        is unchanged — the stamped copy replaces it in the aggregate's
        event list. The copy is deep, so it shares no mutable payload
        (a list, a dict) with the original.

        Only the two ids are validated, each against its own field's type
        and constraints: a UUID string is converted to a ``UUID``, and any
        other value raises :class:`pydantic.ValidationError`. No model
        validator of the event runs, as in 0.3.0 (ADR-070).
        """
        update = {
            "correlation_id": _id_adapter(type(self), "correlation_id").validate_python(
                correlation_id
            ),
            "causation_id": _id_adapter(type(self), "causation_id").validate_python(
                causation_id
            ),
        }
        return self.model_copy(update=update, deep=True)

    @classmethod
    def configure(cls, *, id_generator: IdGenerator[UUID]) -> None:
        """Set the system-wide ID generator for Domain Events.

        Call once at application startup. Affects all ``DomainEvent``
        subclasses that use auto-generated event IDs, unless the subclass
        sets its own ``_id_generator``.
        """
        DomainEvent._id_generator = id_generator


_ID_ADAPTERS: dict[tuple[type[DomainEvent], str], TypeAdapter[Any]] = {}


def _id_adapter(event_type: type[DomainEvent], name: str) -> TypeAdapter[Any]:
    """The validator of one tracing-id field of *event_type*, built once.

    It validates the field's annotation with the field's own constraints,
    so ``stamp()`` checks an id the way construction does without running
    the event's model validators.
    """
    key = (event_type, name)
    adapter = _ID_ADAPTERS.get(key)
    if adapter is None:
        field = event_type.model_fields[name]
        adapter = TypeAdapter(Annotated[field.annotation, field])
        _ID_ADAPTERS[key] = adapter
    return adapter
