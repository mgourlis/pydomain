from __future__ import annotations

from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pydomain.ddd.id_generator import IdGenerator, Uuid7Generator


class CommandResult(BaseModel):
    """Abstract base for command results.

    Each concrete ``Command`` declares the exact result type its handler
    will produce, making ``dispatch()`` return type explicit and safe.
    """

    model_config = ConfigDict(frozen=True)


class EmptyCommandResult(CommandResult):
    """Void-style result for commands that produce no meaningful output.

    The equivalent of ``void`` in C# or ``None`` in Python.
    """


class Command[TResult: CommandResult](BaseModel):
    """Base class for commands with generic result type binding.

    A ``Command`` expresses intent — "do this." Named in imperative mood.
    Carries all data the handler needs. One command modifies exactly one
    aggregate.

    Usage::

        class PlaceOrder(Command[PlaceOrderResult]):
            order_id: UUID
            customer_id: UUID
            items: list[OrderLine]

    When the model is validated, a missing ``command_id`` comes from
    ``cls._id_generator``: the generator of the nearest class that sets
    its own, through the ``_id_generator`` class variable or
    ``configure()`` on that class, else the base class's (ADR-070,
    ADR-078). ``model_construct()`` skips validation, so its ``command_id``
    comes from the base class's generator.
    """

    _id_generator: ClassVar[IdGenerator[UUID]] = Uuid7Generator()

    command_id: UUID = Field(default_factory=lambda: Command._id_generator.generate())

    # ── Distributed Tracing ─────────────────────────────────────────
    # Optional fields — when set by the saga manager, the CommandBus
    # propagates them so events produced by the handler carry the
    # original correlation chain. When None (default), the bus uses
    # ``command_id`` — fully backward compatible.
    correlation_id: UUID | None = None
    causation_id: UUID | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _ensure_command_id(cls, data: Any) -> Any:
        if isinstance(data, dict) and "command_id" not in data:
            return {**data, "command_id": cls._id_generator.generate()}  # pyright: ignore[reportUnknownVariableType]
        return data

    @classmethod
    def configure(cls, *, id_generator: IdGenerator[UUID]) -> None:
        """Set the ID generator of this class and its subclasses.

        ``Command.configure(...)`` sets the default for every subclass
        without its own generator; call it once at application startup.
        Called on a subclass, it sets the generator of that subclass and
        its subclasses only, exactly as declaring ``_id_generator`` on it
        does. A class with its own generator, declared or configured, keeps
        it when a base class is configured later (ADR-078).
        """
        cls._id_generator = id_generator
