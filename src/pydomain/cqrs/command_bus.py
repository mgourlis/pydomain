"""Command bus for the CQRS layer.

The ``CommandBus`` routes commands to their single registered handler and
returns a typed result. Commands run inside a Unit of Work context.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from pydomain.cqrs.behaviors import (
    _COMMITTED_MARK,
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
    PipelineReentryError,
)
from pydomain.cqrs.handlers import CommandHandler
from pydomain.cqrs.unit_of_work import UnitOfWork
from pydomain.ddd.domain_event import DomainEvent


class _CommandPipeline(MessagePipeline):
    """A command pipeline whose terminal step commits after the handler (ADR-079).

    The step awaits the handler, then ``uow.commit()``, then sets
    ``ctx.new_events``, so every behavior's code after ``next()`` runs after
    the commit. ``ctx.handler`` stays the registered handler. The step runs
    at most once per dispatch: a second ``next()`` raises
    ``PipelineReentryError`` before the handler (ADR-079, B-111).
    """

    async def execute(self, ctx: MessageContext, message: Any) -> Any:
        """Refuse: the inherited step runs the handler and never commits."""
        raise TypeError("a command pipeline runs through run(), not execute()")

    async def run(
        self, ctx: MessageContext, message: Any
    ) -> tuple[Any, list[DomainEvent]]:
        """Run the pipeline; return the result and the committed events.

        The step reads ``ctx.uow`` when it runs, so a behavior that replaces
        it before ``next()`` hands its object to the handler and the commit.
        The events are the step's own copy, taken right after the commit, so
        a behavior that changes ``ctx.new_events`` does not change them; they
        are empty when the step never ran.
        """
        committed: list[DomainEvent] = []
        entered = False

        async def terminal() -> Any:
            nonlocal entered
            if entered:
                raise PipelineReentryError(
                    "next() was called a second time in a command pipeline"
                )
            entered = True
            uow = cast(UnitOfWork, ctx.uow)
            result = await self._handler(message, uow)
            try:
                await uow.commit()
            except BaseException:
                # A cancellation after a completed _commit() (ADR-079 point 6),
                # or a handler that committed itself: the scope is committed.
                if getattr(uow, "_committed", False) is True:
                    _mark_committed(ctx, uow, result)
                raise
            committed.extend(_mark_committed(ctx, uow, result))
            return result

        result = await self._run(ctx, terminal)
        return result, committed


def _mark_committed(
    ctx: MessageContext, uow: UnitOfWork, result: Any
) -> list[DomainEvent]:
    """Set ``ctx.new_events`` and the private committed mark; return the events.

    The mark carries the handler's result, for ``IdempotencyBehavior`` to
    record a committed command whose ``next()`` raises (B-121). It is a
    private attribute set with ``setattr``, not a ``MessageContext`` field
    (B-118).
    """
    events = uow.collect_events()
    ctx.new_events = list(events)
    setattr(ctx, _COMMITTED_MARK, result)
    return events


@dataclass
class _HandlerEntry:
    """Internal type pairing a pipeline with its UoW factory."""

    pipeline: _CommandPipeline
    uow_factory: Callable[[], UnitOfWork]


class CommandBus:
    """Routes commands to their single handler and returns a typed result.

    Commands run inside a Unit of Work context. The UoW is created by
    the factory registered alongside the handler — ``dispatch()`` does
    not accept a UoW parameter. The commit runs in the pipeline's
    terminal step, right after the handler and inside every behavior;
    the handler never commits (ADR-020, ADR-079).

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
            pipeline=_CommandPipeline(handler=handler, behaviors=behaviors),
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
        4. Run the pipeline: the behaviors in order, then the terminal
           step, which awaits the handler, then ``uow.commit()`` (which
           stamps the events with the tracing IDs), then sets
           ``ctx.new_events``. Each behavior's code after ``next()`` runs
           after the commit (ADR-079). A pipeline that returns without
           reaching the terminal step (a cached duplicate) commits
           nothing; the scope rolls back on exit.
        5. On failure (of a behavior, the handler or the commit): leave
           the context, whose ``__aexit__`` rolls back once, and raise
           ``CommandExecutionError``.

        Returns a ``(result, new_events)`` tuple where ``new_events``
        are the terminal step's own copy of the committed domain events
        (a behavior that changes ``ctx.new_events`` does not change it), each
        stamped with ``correlation_id`` and ``causation_id``; empty when
        the handler was not reached.

        Raises:
            CommandExecutionError: if the pipeline, the handler or
                ``commit()`` raises. The original exception is its
                ``__cause__``. If the clean-up on exit raises as well (the
                rollback, or the drain of a committed scope), that
                exception is its ``__context__``, and a note names its
                type only (``clean-up also failed: <type>``). A handler
                that commits itself (against ADR-020) makes the terminal
                step's commit raise ``UnitOfWorkAlreadyCommittedError``, wrapped
                the same way: nothing is written twice, the handler's
                commit stays persisted, and the scope is not rolled back.
            Exception: from the drain on exit of a dispatch that
                committed successfully, unwrapped. No events are returned,
                though the commit persisted (ADR-068).
            BaseException: a cancellation (any ``BaseException`` that is
                not an ``Exception``) is not wrapped. If the clean-up on
                exit raises as well, the cancellation still propagates, with
                the same type-only note; the clean-up's exception is its
                ``__context__`` (ADR-078).
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

        # The UoW's __aexit__ rolls back a scope that did not commit, so the
        # bus never calls rollback() itself: a failed dispatch rolls back once
        # (ADR-068). The failure is wrapped outside the ``async with`` block,
        # so a rollback that raises as well cannot replace it: it becomes the
        # CommandExecutionError's __context__, the failure its __cause__.
        failure: BaseException | None = None
        try:
            async with uow:
                try:
                    result, events = await entry.pipeline.run(ctx, command)
                except BaseException as exc:
                    failure = exc
                    raise
        except Exception as exc:
            if failure is None:
                raise
            # The clean-up on exit (the rollback, or a committed scope's
            # drain) failed too, unless exc is the failure itself. Name its
            # type only: its message may carry content (ADR-068).
            note = f"clean-up also failed: {type(exc).__name__}"
            if not isinstance(failure, Exception):
                # A cancellation stays a cancellation (ADR-078, B-88): the
                # clean-up error is its hidden __context__, and its own
                # __cause__, if any, is kept.
                failure.add_note(note)
                raise failure from failure.__cause__
            error = CommandExecutionError(command)
            if exc is not failure:
                error.add_note(note)
            raise error from failure
        if failure is not None:  # an __aexit__ that suppressed the failure
            if not isinstance(failure, Exception):
                raise failure  # a cancellation is never swallowed or wrapped
            raise CommandExecutionError(command) from failure

        return (result if result is not None else EmptyCommandResult()), events
