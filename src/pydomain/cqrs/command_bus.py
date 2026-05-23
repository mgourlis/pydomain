"""Command bus for the CQRS layer.

The ``CommandBus`` routes commands to their single registered handler and
returns a typed result. Commands run inside a Unit of Work context.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydomain.cqrs.behaviors import (
    MessageContext,
    MessageKind,
    MessagePipeline,
    PipelineBehavior,
)
from pydomain.cqrs.commands import Command, CommandResult, EmptyCommandResult
from pydomain.cqrs.exceptions import (
    CommandExecutionError,
    HandlerAlreadyRegisteredError,
    NoHandlerRegisteredError,
)
from pydomain.cqrs.handlers import CommandHandler
from pydomain.cqrs.unit_of_work import UnitOfWork
from pydomain.ddd.domain_event import DomainEvent


@dataclass
class _HandlerEntry:
    """Internal type pairing a pipeline with its UoW factory."""

    pipeline: MessagePipeline
    uow_factory: Callable[[], UnitOfWork]


class CommandBus:
    """Routes commands to their single handler and returns a typed result.

    Commands run inside a Unit of Work context. The UoW is created by
    the factory registered alongside the handler — ``dispatch()`` does
    not accept a UoW parameter.

    Type safety is provided by the ``CommandHandler`` protocol, which
    downstream code can use with ``isinstance`` checks. The bus itself
    accepts ``CommandHandler`` instances at registration time — type
    erasure to ``Callable`` happens inside ``MessagePipeline``.
    """

    def __init__(self) -> None:
        self._handlers: dict[type[Command[Any]], _HandlerEntry] = {}

    def register[TCommand: Command[CommandResult], TResult: CommandResult](
        self,
        command_type: type[TCommand],
        handler: CommandHandler[TCommand, TResult],
        uow_factory: Callable[[], UnitOfWork],
        behaviors: list[PipelineBehavior] | None = None,
    ) -> None:
        """Register a handler and UoW factory for a command type.

        Parameters
        ----------
        command_type:
            The command class to register the handler for.
        handler:
            A ``CommandHandler`` that receives a command instance and
            the transaction-scoped ``UnitOfWork``, and returns a
            ``CommandResult``.
        uow_factory:
            A callable that creates a fresh ``UnitOfWork`` per dispatch.
            The UoW provides transactional scope and event collection.
        behaviors:
            Optional list of pipeline behaviors that wrap the handler in
            onion order.

        Raises ``HandlerAlreadyRegisteredError`` if a handler is already
        registered for ``command_type``.
        """
        if command_type in self._handlers:
            raise HandlerAlreadyRegisteredError(
                f"Handler already registered for {command_type.__name__}"
            )
        self._handlers[command_type] = _HandlerEntry(
            pipeline=MessagePipeline(handler=handler, behaviors=behaviors),
            uow_factory=uow_factory,
        )

    async def dispatch(
        self,
        command: Command[Any],
    ) -> tuple[CommandResult, list[DomainEvent]]:
        """Dispatch a command to its handler.

        The UoW is created from the factory registered with the handler.
        Every command runs inside a UoW context:

        1. Create UoW via the registered factory.
        2. Enter UoW context (``async with uow``).
        3. Build ``MessageContext`` with the command, handler, and UoW.
        4. Run the pipeline (behaviors in order, then terminal handler).
        5. On success: ``uow.commit()``, stamp tracing IDs on events.
        6. On failure: ``uow.rollback()`` and re-raise.

        Returns a ``(result, new_events)`` tuple where ``new_events``
        are the domain events produced by the handler, each stamped
        with ``correlation_id`` and ``causation_id``.
        """
        entry = self._handlers.get(type(command))
        if entry is None:
            raise NoHandlerRegisteredError(
                f"No handler registered for {type(command).__name__}"
            )

        uow = entry.uow_factory()

        # Resolve tracing IDs: prefer explicit values on the command
        # (set by saga manager), otherwise fall back to command_id.
        # This keeps full backward compatibility — commands without
        # correlation_id/causation_id behave exactly as before.
        correlation_id = command.correlation_id or command.command_id
        causation_id = command.causation_id or command.command_id

        # Stamp the command's tracing IDs onto the UoW so events collected
        # during commit() carry the correct correlation/causation chain.
        # Uses setattr() because the parameter type is the UnitOfWork Protocol,
        # which does not declare these private attributes — at runtime the
        # object is always an AbstractUnitOfWork that does.
        setattr(uow, "_correlation_id", correlation_id)
        setattr(uow, "_causation_id", causation_id)

        ctx = MessageContext(
            message=command,
            kind=MessageKind.COMMAND,
            uow=uow,
            correlation_id=correlation_id,
            causation_id=causation_id,
            metadata={"command_id": command.command_id},
        )

        async with uow:
            try:
                result = await entry.pipeline.execute(ctx, command)
                await uow.commit()

                raw_events = uow.collect_events()
                ctx.new_events = raw_events

                return (
                    result if result is not None else EmptyCommandResult()
                ), raw_events

            except Exception as exc:
                await uow.rollback()
                raise CommandExecutionError(command) from exc
