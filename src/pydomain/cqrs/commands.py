from __future__ import annotations

from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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

    @classmethod
    def configure(cls, *, id_generator: IdGenerator[UUID]) -> None:
        """Set the system-wide ID generator for Commands.

        Call once at application startup. Affects all ``Command``
        subclasses that use auto-generated command IDs.
        """
        Command._id_generator = id_generator
