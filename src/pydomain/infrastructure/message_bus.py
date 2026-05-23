"""Level 3 facade wrapping CommandBus, QueryBus, and event dispatcher.

The ``MessageBus`` is the top-level entry point for the CQRS message
infrastructure. It routes:

- **Commands** to the internal ``CommandBus``, which creates a UoW from the
  registered factory, runs the handler inside it, and collects emitted domain
  events. The collected events are then dispatched to registered event handlers.
- **Queries** to the internal ``QueryBus``, which runs them without a Unit of
  Work (read-only path).

Use the unified ``dispatch()`` method for both commands and queries — the
bus inspects the message type and routes accordingly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from pydomain.cqrs.behaviors import (
    MessageContext,
    MessageKind,
    MessagePipeline,
    PipelineBehavior,
)
from pydomain.cqrs.command_bus import CommandBus
from pydomain.cqrs.commands import Command, CommandResult
from pydomain.cqrs.handlers import CommandHandler, EventHandler, QueryHandler
from pydomain.cqrs.queries import Query, QueryResult
from pydomain.cqrs.query_bus import QueryBus
from pydomain.cqrs.unit_of_work import UnitOfWork
from pydomain.ddd.domain_event import DomainEvent

logger = logging.getLogger("pydomain.message_bus")


class MessageBus:
    """Level 3 facade wrapping CommandBus + QueryBus + event dispatcher.

    The MessageBus is the primary entry point for application-level message
    dispatch. It delegates commands and queries to their respective buses and
    manages the event handler lifecycle after command execution.

    Use ``dispatch()`` for both commands and queries — the bus inspects
    the message type and routes accordingly.

    Parameters
    ----------
    command_bus:
        Optional pre-configured ``CommandBus`` instance. A new instance is
        created internally if not provided.
    query_bus:
        Optional pre-configured ``QueryBus`` instance. A new instance is
        created internally if not provided.
    """

    def __init__(
        self,
        command_bus: CommandBus | None = None,
        query_bus: QueryBus | None = None,
    ) -> None:
        self._command_bus = command_bus or CommandBus()
        self._query_bus = query_bus or QueryBus()
        self._event_handlers: dict[type[DomainEvent], list[MessagePipeline]] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_command[TCommand: Command[CommandResult], TResult: CommandResult](
        self,
        command_type: type[TCommand],
        handler: CommandHandler[TCommand, TResult],
        uow_factory: Callable[[], UnitOfWork],
        behaviors: list[PipelineBehavior] | None = None,
    ) -> None:
        """Register a handler and UoW factory for a command type.

        Delegates to the internal ``CommandBus.register()``.

        Parameters
        ----------
        command_type:
            The command class to register the handler for.
        handler:
            A ``CommandHandler`` that receives a command instance and
            returns a ``CommandResult``.
        uow_factory:
            A callable that creates a fresh ``UnitOfWork`` per dispatch.
        behaviors:
            Optional list of pipeline behaviors that wrap the handler in
            onion order.

        Raises ``HandlerAlreadyRegisteredError`` if a handler is already
        registered for ``command_type``.
        """
        self._command_bus.register(command_type, handler, uow_factory, behaviors)

    def register_query[TQuery: Query[QueryResult], TResult: QueryResult](
        self,
        query_type: type[TQuery],
        handler: QueryHandler[TQuery, TResult],
        behaviors: list[PipelineBehavior] | None = None,
    ) -> None:
        """Register a handler for a query type.

        Delegates to the internal ``QueryBus.register()``.

        Raises ``HandlerAlreadyRegisteredError`` if a handler is already
        registered for ``query_type``.

        Parameters
        ----------
        query_type:
            The query class to register the handler for.
        handler:
            A ``QueryHandler`` that receives a query instance and returns
            a ``QueryResult``.
        behaviors:
            Optional list of pipeline behaviors that wrap the handler in
            onion order.
        """
        self._query_bus.register(query_type, handler, behaviors)

    def register_event[TEvent: DomainEvent](
        self,
        event_type: type[TEvent],
        handler: EventHandler[TEvent],
        behaviors: list[PipelineBehavior] | None = None,
    ) -> None:
        """Register an event handler.

        Multiple handlers can be registered for the same event type.
        Handlers are invoked in registration order.

        The handler is wrapped in a ``MessagePipeline`` at registration
        time. If pipeline behaviors are provided, they are composed around
        the handler in onion order (first behavior is outermost).

        Parameters
        ----------
        event_type:
            The domain event class to handle.
        handler:
            An ``EventHandler`` that receives a domain event instance.
            Event handlers return ``None`` (fire-and-forget).
        behaviors:
            Optional list of pipeline behaviors that wrap the handler.
        """
        self._event_handlers.setdefault(event_type, []).append(
            MessagePipeline(handler=handler, behaviors=behaviors),
        )

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, message: Command[Any] | Query[Any] | DomainEvent) -> Any:
        """Dispatch a command, query, or domain event.

        Inspects the message type and routes accordingly:

        - **Command**: dispatched to ``CommandBus``, which creates a UoW
          from the registered factory, runs the handler, and collects
          domain events. Collected events are dispatched to registered
          event handlers.
        - **Query**: dispatched to ``QueryBus`` (read-only, no UoW).
        - **DomainEvent**: dispatched directly to registered event handlers
          (no UoW). Used by the ``InboundEventGateway`` for events that
          arrived from external message brokers.

        Parameters
        ----------
        message:
            A ``Command``, ``Query``, or ``DomainEvent`` instance.

        Returns
        -------
        Any
            ``CommandResult`` or ``QueryResult`` for messages, ``None`` for
            domain events.

        Raises
        ------
        TypeError
            If *message* is neither a ``Command``, ``Query``, nor
            ``DomainEvent``.
        """
        if isinstance(message, DomainEvent):
            await self._dispatch_event(message)
            return None
        if isinstance(message, Command):
            result, events = await self._command_bus.dispatch(message)
            await self._dispatch_events(events)
            return result
        if isinstance(message, Query):  # pyright: ignore[reportUnnecessaryIsInstance]
            return await self._query_bus.dispatch(message)
        raise TypeError(
            f"Expected Command, Query, or DomainEvent, got {type(message).__name__}"
        )

    # ------------------------------------------------------------------
    # Event dispatch internals
    # ------------------------------------------------------------------

    async def _dispatch_events(self, events: list[DomainEvent]) -> None:
        """Dispatch events sequentially, one at a time.

        Event N+1 handlers run only after ALL handlers for event N finish.
        No concurrency -- events are dispatched in strict sequence.
        """
        for event in events:
            await self._dispatch_event(event)

    async def _dispatch_event(self, event: DomainEvent) -> None:
        """Dispatch a single event to its registered handlers.

        Characteristics
        ---------------
        - **Failure isolation:** each handler's failure is logged and
          swallowed; remaining handlers continue uninterrupted.
        - **Sequential within event:** handlers run one at a time.
        - **No UoW:** event handlers manage their own persistence and are
          not passed a Unit of Work.
        """
        pipelines = self._event_handlers.get(type(event), [])
        for pipeline in pipelines:
            ctx = MessageContext(
                message=event,
                kind=MessageKind.EVENT,
                uow=None,
                correlation_id=event.correlation_id,
                causation_id=event.causation_id,
                metadata={},
            )
            try:
                await pipeline.execute(ctx, event)
            except Exception:
                logger.exception(
                    "Event handler %s failed for %s",
                    getattr(ctx.handler, "__name__", str(pipeline)),
                    type(event).__name__,
                )
