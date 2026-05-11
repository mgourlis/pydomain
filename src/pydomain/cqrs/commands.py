from typing import ClassVar, Generic, TypeVar
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


TResult = TypeVar("TResult", bound=CommandResult)


class Command(BaseModel, Generic[TResult]):
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

    _id_generator: ClassVar[IdGenerator] = Uuid7Generator()

    command_id: UUID = Field(default_factory=lambda: Command._id_generator.generate())

    model_config = ConfigDict(frozen=True, extra="forbid")
