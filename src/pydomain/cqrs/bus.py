from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Protocol, TypeVar, runtime_checkable
from uuid import UUID

from pydomain.cqrs.commands import Command, CommandResult, EmptyCommandResult
from pydomain.cqrs.exceptions import (
    HandlerAlreadyRegisteredError,
    NoHandlerRegisteredError,
)
from pydomain.ddd.domain_event import DomainEvent


class MessageKind(Enum):
    """Distinguishes message categories in pipeline behaviors."""

    COMMAND = auto()
    EVENT = auto()
    QUERY = auto()


@runtime_checkable
class UnitOfWork(Protocol):
    """Protocol for Unit of Work implementations.

    The UoW manages transactional scope and domain event collection.
    """

    async def __aenter__(self) -> UnitOfWork: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_val: BaseException | None = None,
        exc_tb: Any | None = None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    def collect_events(self) -> list[DomainEvent]: ...


@dataclass
class MessageContext:
    """Mutable carrier that flows through the pipeline.

    Every behavior and the terminal handler receive the same context
    instance so behaviors can pass data downstream via ``metadata``.
    """

    message: Any
    handler: Callable[..., Any]
    kind: MessageKind = MessageKind.COMMAND
    uow: Any | None = None
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    new_events: list[DomainEvent] = field(default_factory=list)


@runtime_checkable
class NextHandler(Protocol):
    async def __call__(self) -> Any: ...


@runtime_checkable
class PipelineBehavior(Protocol):
    """Protocol for pipeline behaviors.

    Behaviors wrap message handlers in a decorator (onion) pattern.
    Each behavior runs before and after calling ``next()``.
    """

    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        """Run logic before and/or after calling ``next()``."""
        ...


TCommand = TypeVar("TCommand", bound=Command[Any])
TResult = TypeVar("TResult", bound=CommandResult)


def _stamp_events(
    events: list[DomainEvent],
    *,
    correlation_id: UUID,
    causation_id: UUID,
) -> list[DomainEvent]:
    """Return stamped copies of domain events with tracing IDs."""
    return [
        e.stamp(correlation_id=correlation_id, causation_id=causation_id)
        for e in events
    ]


class CommandBus:
    """Routes commands to their single handler and returns a typed result.

    Commands run inside a Unit of Work context. The UoW is baked into
    ``dispatch()``, not a pipeline behavior.
    """

    def __init__(self) -> None:
        self._handlers: dict[
            type[Command[Any]],
            tuple[Callable[..., Any], list[PipelineBehavior]],
        ] = {}

    def register(
        self,
        command_type: type[Command[Any]],
        handler: Callable[..., Any],
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
        self._handlers[command_type] = (handler, behaviors or [])

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
        entry = self._handlers.get(type(command))
        if entry is None:
            raise NoHandlerRegisteredError(
                f"No handler registered for {type(command).__name__}"
            )

        handler, behaviors = entry

        async def terminal() -> Any:
            return await handler(command)

        ctx = MessageContext(
            message=command,
            handler=handler,
            kind=MessageKind.COMMAND,
            uow=uow,
            correlation_id=command.command_id,
            causation_id=command.command_id,
        )

        async with uow:
            try:
                result = await _run_pipeline(behaviors, ctx, terminal)
                await uow.commit()

                raw_events = uow.collect_events()
                stamped_events = _stamp_events(
                    raw_events,
                    correlation_id=ctx.correlation_id or command.command_id,
                    causation_id=ctx.causation_id or command.command_id,
                )
                ctx.new_events = stamped_events

                return (
                    result if result is not None else EmptyCommandResult()
                ), stamped_events

            except Exception:
                await uow.rollback()
                raise


async def _run_pipeline(
    behaviors: list[PipelineBehavior],
    ctx: MessageContext,
    terminal: Callable[[], Any],
) -> Any:
    """Execute the pipeline chain: outermost behavior → ... → terminal handler."""
    chain: Callable[[], Any] = terminal
    for behavior in reversed(behaviors):
        prev = chain
        chain = _wrap_behavior(behavior, prev, ctx)
    return await chain()


def _wrap_behavior(
    behavior: PipelineBehavior,
    next_handler: Callable[[], Any],
    ctx: MessageContext,
) -> Callable[[], Any]:
    """Wrap a single behavior around the next handler in the chain."""

    async def wrapper() -> Any:
        return await behavior.handle(ctx, next_handler)

    return wrapper
