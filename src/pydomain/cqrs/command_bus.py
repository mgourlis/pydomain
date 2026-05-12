"""Command bus for the CQRS layer.

The ``CommandBus`` routes commands to their single registered handler and
returns a typed result. Commands run inside a Unit of Work context.
"""

from __future__ import annotations

from collections.abc import Callable
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
from pydomain.cqrs.unit_of_work import UnitOfWork
from pydomain.ddd.domain_event import DomainEvent


class CommandBus:
    """Routes commands to their single handler and returns a typed result.

    Commands run inside a Unit of Work context. The UoW is baked into
    ``dispatch()``, not a pipeline behavior.

    Type safety is provided by the ``CommandHandler`` protocol, which
    downstream code can use with ``isinstance`` checks. The bus itself
    uses ``Callable[[Any], Any]`` because handlers are heterogeneous
    and stored in a single dict — type erasure is inevitable here.
    """

    def __init__(self) -> None:
        self._handlers: dict[type[Command[Any]], MessagePipeline] = {}

    def register(
        self,
        command_type: type[Command[Any]],
        handler: Callable[[Any], Any],
        behaviors: list[PipelineBehavior] | None = None,
    ) -> None:
        """Register a handler for a command type.

        Raises ``HandlerAlreadyRegisteredError`` if a handler is already
        registered for ``command_type``.
        """
        if command_type in self._handlers:
            raise HandlerAlreadyRegisteredError(
                f"Handler already registered for {command_type.__name__}"
            )
        self._handlers[command_type] = MessagePipeline(
            handler=handler, behaviors=behaviors
        )

    async def dispatch(
        self,
        command: Command[Any],
        uow: UnitOfWork,
    ) -> tuple[CommandResult, list[DomainEvent]]:
        """Dispatch a command to its handler.

        Every command runs inside a UoW context:
        1. Enter UoW context (``async with uow``).
        2. Build ``MessageContext`` with the command, handler, and UoW.
        3. Run the pipeline (behaviors in order, then terminal handler).
        4. On success: ``uow.commit()``, stamp tracing IDs on events.
        5. On failure: ``uow.rollback()`` and re-raise.

        Returns a ``(result, new_events)`` tuple where ``new_events``
        are the domain events produced by the handler, each stamped
        with ``correlation_id`` and ``causation_id``.
        """
        pipeline = self._handlers.get(type(command))
        if pipeline is None:
            raise NoHandlerRegisteredError(
                f"No handler registered for {type(command).__name__}"
            )

        # Stamp the command's tracing IDs onto the UoW so events collected
        # during commit() carry the correct correlation/causation chain.
        # Uses setattr() because the parameter type is the UnitOfWork Protocol,
        # which does not declare these private attributes — at runtime the
        # object is always an AbstractUnitOfWork that does.
        setattr(uow, "_correlation_id", command.command_id)
        setattr(uow, "_causation_id", command.command_id)

        ctx = MessageContext(
            message=command,
            kind=MessageKind.COMMAND,
            uow=uow,
            correlation_id=command.command_id,
            causation_id=command.command_id,
            metadata={"command_id": str(command.command_id)},
        )

        async with uow:
            try:
                result = await pipeline.execute(ctx, command)
                await uow.commit()

                raw_events = uow.collect_events()
                ctx.new_events = raw_events

                return (
                    result if result is not None else EmptyCommandResult()
                ), raw_events

            except Exception as exc:
                await uow.rollback()
                raise CommandExecutionError(command) from exc
