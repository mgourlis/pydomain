from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydomain.cqrs.commands import Command, CommandResult
from pydomain.cqrs.queries import Query, QueryResult
from pydomain.cqrs.unit_of_work import UnitOfWork
from pydomain.ddd.domain_event import DomainEvent


@runtime_checkable
class CommandHandler[
    TCommand: Command[CommandResult],
    TResult: CommandResult,
](Protocol):
    """Protocol for command handlers.

    A command handler receives a command **and** the Unit of Work that
    provides the transactional scope for the dispatch.  The handler
    accesses repositories through the UoW's public attributes (e.g.
    ``uow.orders``, ``uow.customers``) to load and persist aggregates.

    The handler must **not** call ``uow.commit()`` or
    ``uow.rollback()`` — the ``CommandBus`` manages the lifecycle.

    Handlers are registered with the ``CommandBus`` via ``register()``.
    """

    async def __call__(self, command: TCommand, uow: UnitOfWork) -> TResult:
        """Execute the handler logic inside the given UoW scope."""
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


@runtime_checkable
class EventHandler[TEvent: DomainEvent](Protocol):
    """Protocol for event handlers.

    An event handler receives a domain event and performs side effects
    (email, notifications, projections, orchestrations). Event handlers
    return ``None`` — they are fire-and-forget.

    Multiple handlers can be registered for the same event type.
    Handlers fail independently — one handler's failure does not affect
    other handlers for the same event.

    For orchestrations (dispatching new commands from an event handler),
    inject the ``MessageBus`` via the handler's constructor::

        class SendWelcomeEmailHandler:
            def __init__(self, bus: MessageBus) -> None:
                self._bus = bus

            async def __call__(self, event: UserCreated) -> None:
                await email_service.send_welcome(event.email)
                await self._bus.dispatch(CreateWelcomeDiscount(...))

    Handlers are registered with the ``MessageBus`` via
    ``register_event()``.
    """

    async def __call__(self, event: TEvent) -> None:
        """React to the domain event."""
        ...
