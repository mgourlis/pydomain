from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from copy import copy
from datetime import UTC, datetime
from functools import cache
from typing import Annotated, Any, ClassVar, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    PrivateAttr,
    TypeAdapter,
    model_validator,
)

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
    ``cls._id_generator``: the generator of the nearest class that sets
    its own, through the ``_id_generator`` class variable or
    ``configure()`` on that class, else the base class's (ADR-070,
    ADR-078). ``model_construct()`` skips validation, so its ``event_id``
    comes from the base class's generator.

    A generated ``event_id`` counts as set: it is in ``model_fields_set`` and
    in ``model_dump(exclude_unset=True)``. The event also records, in the
    private attribute ``_event_id_generated``, that its id was generated, so
    the ``InboundEventGateway`` can replace a generated id with one derived
    from the integration event and keep an id the translator set (ADR-080
    point 1):

    - the id is generated when the validated input has no ``event_id``, once
      every ``mode="before"`` validator of a subclass has run. An id that a
      subclass's own before-validator sets, or one passed under the field's
      alias, counts as explicit;
    - ``model_copy()`` and :meth:`stamp` keep the record, except that
      ``model_copy(update={"event_id": ...})`` makes the id explicit;
    - validating an existing instance (``Placed.model_validate(event)``, or
      an event put into a field of another model) returns that instance and
      keeps its record; an event validated from a dict inside another model
      gets its own;
    - ``model_construct()`` runs no validator, so its id reads as explicit.

    Equality ignores the record: an event equals its round trip through
    ``model_dump()`` or JSON, whose id is then explicit. The hash covers the
    fields only, as for every frozen model.
    """

    _id_generator: ClassVar[IdGenerator[UUID]] = Uuid7Generator()

    event_id: UUID = Field(default_factory=lambda: DomainEvent._id_generator.generate())
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_version: int = 1
    correlation_id: UUID | None = None
    causation_id: UUID | None = None

    _event_id_generated: bool = PrivateAttr(default=False)

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="wrap")
    @classmethod
    def _mark_generated_event_id(
        cls, data: Any, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        # Pydantic runs this before any before-validator, subclass ones
        # included, so the generation is reported, not computed, here.
        token = _GENERATED_EVENT_ID.set(None)
        try:
            event = handler(data)
            generated = _GENERATED_EVENT_ID.get()
            # Only ever set the marker: an existing instance comes back as
            # itself, and writing False would make its generated id explicit.
            if generated is not None and event.event_id == generated:
                event._event_id_generated = True
            return event
        finally:
            _GENERATED_EVENT_ID.reset(token)

    @model_validator(mode="before")
    @classmethod
    def _ensure_event_id(cls, data: Any) -> Any:
        if isinstance(data, dict) and "event_id" not in data:
            event_id = cls._id_generator.generate()
            _GENERATED_EVENT_ID.set(event_id)
            return {**data, "event_id": event_id}  # pyright: ignore[reportUnknownVariableType]
        return data

    def __eq__(self, other: object) -> bool:
        # Pydantic compares private attributes too; the marker is not part of
        # the event (ADR-080, B-158). __hash__ is left to Pydantic.
        if (
            isinstance(other, DomainEvent)
            and other._event_id_generated != self._event_id_generated
        ):
            other = copy(other)
            other._event_id_generated = self._event_id_generated
        return super().__eq__(other)

    def model_copy(
        self, *, update: Mapping[str, Any] | None = None, deep: bool = False
    ) -> Self:
        """Return a copy, as :meth:`pydantic.BaseModel.model_copy` does.

        An ``event_id`` in *update* counts as explicit (ADR-080, B-166).
        """
        event = super().model_copy(update=update, deep=deep)
        if update is not None and "event_id" in update:
            event._event_id_generated = False
        return event

    def stamp(self, *, correlation_id: UUID, causation_id: UUID) -> Self:
        """Return a new frozen copy with tracing IDs set.

        Called by the UnitOfWork during ``commit()``. The original event
        is unchanged — the stamped copy replaces it in the aggregate's
        event list. The copy is deep, so it shares no mutable payload
        (a list, a dict) with the original.

        Only the two ids are validated, each against its own field's type
        and constraints: a UUID string is converted to a ``UUID``, and any
        other value raises :class:`pydantic.ValidationError`. No model
        validator of the event runs, as in 0.3.0, and neither does a
        ``@field_validator`` method that a subclass declares for
        ``correlation_id`` or ``causation_id`` (ADR-070).
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
        """Set the ID generator of this class and its subclasses.

        ``DomainEvent.configure(...)`` sets the default for every subclass
        without its own generator; call it once at application startup.
        Called on a subclass, it sets the generator of that subclass and
        its subclasses only, exactly as declaring ``_id_generator`` on it
        does. A class with its own generator, declared or configured, keeps
        it when a base class is configured later (ADR-078).
        """
        cls._id_generator = id_generator


# The event_id that the innermost DomainEvent validation generated, if any.
_GENERATED_EVENT_ID: ContextVar[UUID | None] = ContextVar(
    "pydomain_generated_event_id", default=None
)


@cache
def _id_adapter(event_type: type[DomainEvent], name: str) -> TypeAdapter[Any]:
    """The validator of one tracing-id field of *event_type*, built once.

    It validates the field's annotation with the field's own constraints,
    so ``stamp()`` checks an id the way construction does without running
    the event's model validators.
    """
    field = event_type.model_fields[name]
    annotated: Any = Annotated[field.annotation, field]
    return TypeAdapter(annotated)
