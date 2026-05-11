from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydomain.cqrs.commands import Command, CommandResult
from pydomain.cqrs.queries import Query, QueryResult


@runtime_checkable
class CommandHandler[
    TCommand: Command[CommandResult],
    TResult: CommandResult,
](Protocol):
    """Protocol for command handlers.

    A command handler receives a command and returns a typed result.
    Handlers are registered with the ``CommandBus`` via ``register()``.
    """

    async def __call__(self, command: TCommand) -> TResult:
        """Execute the handler logic and return a result."""
        ...


@runtime_checkable
class QueryHandler[
    TQuery: Query[QueryResult],
    TResult: QueryResult,
](Protocol):
    """Protocol for query handlers.

    A query handler receives a query and returns a typed result.
    Queries are read-only — no side effects, no ``UnitOfWork``.
    Handlers are registered with the ``QueryBus`` via ``register()``.
    """

    async def __call__(self, query: TQuery) -> TResult:
        """Execute the query logic and return a result."""
        ...
