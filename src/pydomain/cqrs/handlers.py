from typing import Protocol, runtime_checkable

from pydomain.cqrs.commands import Command, CommandResult


@runtime_checkable
class CommandHandler[TCommand: Command, TResult: CommandResult](Protocol):
    """Protocol for command handlers.

    A command handler receives a command and returns a typed result.
    Handlers are registered with the ``CommandBus`` via ``register()``.
    """

    async def __call__(self, command: TCommand) -> TResult:
        """Execute the handler logic and return a result."""
        ...
