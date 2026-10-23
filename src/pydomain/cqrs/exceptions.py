from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Self
from uuid import UUID

from pydomain.cqrs.commands import Command
from pydomain.ddd.exceptions import DomainError


class CQRSError(DomainError):
    """Base class for all CQRS-layer errors."""


class HandlerAlreadyRegisteredError(CQRSError):
    """Raised when registering a handler for a message type that already has one."""


class NoHandlerRegisteredError(CQRSError):
    """Raised when dispatching a message with no registered handler."""


class CommandExecutionError(CQRSError):
    """Raised when a command handler raises an exception.

    Wraps the original exception and carries the failed command for diagnostics.
    """

    def __init__(self, command: Command[Any], message: str | None = None) -> None:
        self.command = command
        command_name = type(command).__name__
        super().__init__(
            message if message is not None else f"Command {command_name} failed"
        )


class UnitOfWorkAlreadyCommittedError(CQRSError):
    """Raised when ``commit()`` is called a second time in one unit-of-work scope.

    A scope commits at most once (ADR-068): the second call raises before any
    hook runs, so nothing is flushed, written to the outbox or committed twice.
    If the first commit succeeded, its changes stay persisted; this is what a
    handler that calls ``commit()`` itself (against ADR-020) gets inside a
    dispatch. A retry after a failed ``commit()`` raises too: roll back, or
    enter the unit of work again, to start a new scope.
    """


class LockReentryError(CQRSError):
    """Raised when a task acquires a lock key it already holds.

    Locks are not reentrant (ADR-076): a handler must not dispatch a nested
    command that resolves to a lock key it holds. ``FakeLockProvider`` raises
    this instead of deadlocking. A ``LockProvider`` is not required to raise
    it; another provider may block instead. The message names no key, which
    is built from the message's content.
    """


class PipelineReentryError(CQRSError):
    """Raised when a behavior calls ``next()`` a second time in a command pipeline.

    A command pipeline commits once, in its terminal step (ADR-079), so its
    ``next()`` runs the handler and the commit at most once per dispatch. The
    second call raises before the handler runs; to retry, dispatch the
    command again. The bus wraps it in ``CommandExecutionError`` like every
    pipeline failure. Query and event pipelines allow any number of calls.

    Call ``next()`` at most once and never concurrently: with
    ``asyncio.gather(next(), next())`` the second call is refused, but
    ``gather`` does not cancel the first run, which commits after the
    dispatch was reported failed and rolled back.

    The message carries no content (ADR-071).
    """


class CommandInProgressError(CQRSError):
    """Raised when a command's ``command_id`` is reserved by a dispatch still running.

    :class:`~pydomain.cqrs.behaviors.IdempotencyBehavior` raises it, before
    the handler runs, when its store implements
    :class:`~pydomain.cqrs.idempotency.MessageReservationStore` and another
    dispatch of the same ``command_id`` holds the reservation (ADR-079
    point 5). Nothing of the duplicate runs or commits. The bus wraps it in
    ``CommandExecutionError`` like every pipeline failure. Retry later: once
    the first dispatch completed, the retry gets its cached result; if it
    failed, the retry runs the handler. A reservation whose ``release``
    failed, or whose process crashed, refuses retries until the store's TTL
    frees it.

    The message names the command type only (ADR-071).
    """


class EventInProgressError(CQRSError):
    """Raised when an event's key is reserved by a delivery still running.

    :class:`~pydomain.cqrs.behaviors.EventIdempotencyBehavior` raises it,
    before the handler runs, when its store implements
    :class:`~pydomain.cqrs.idempotency.MessageReservationStore` and another
    delivery of the same event to the same consumer holds the reservation
    (ADR-080 point 4). The duplicate's handler does not run. ``EventBus``
    logs it at ERROR like any handler failure. On a direct dispatch it is a
    member of the :class:`EventHandlersFailed` raised after the other
    handlers ran, so the inbound gateway NACKs it and the broker redelivers
    the event later (ADR-081). A reservation whose ``release`` or ``set``
    failed, or whose process crashed, refuses redeliveries until the store's
    TTL frees it.

    The message names the event type only (ADR-071).
    """


class EventIdempotencyMisuseError(CQRSError):
    """Raised when one ``EventIdempotencyBehavior`` instance serves a second handler.

    An instance serves one handler, so its keys never follow whichever
    handler ran (ADR-080 point 3, B-159). It learns its handler at its first
    dispatch and raises this, before any store call, at the first dispatch
    to a different handler; that handler does not run. Handlers compare by
    identity, then with ``==``, so one function registered twice is one
    handler and is not detected, and neither are callable instances that
    compare equal (B-178). Build one instance per registration. ``EventBus``
    logs it at ERROR like any handler failure; on a direct dispatch it is a
    member of :class:`EventHandlersFailed`, so the inbound gateway NACKs
    every delivery and the broker's dead-letter queue catches the message
    (ADR-081, B-182).

    The message names no content (ADR-071).
    """


class EventHandlersFailed(ExceptionGroup[Exception], CQRSError):
    """Raised by a direct event dispatch once every handler has run, if any failed.

    ``EventBus.dispatch``, ``MessageBus.dispatch(DomainEvent)`` and
    ``Application.dispatch(event)`` run every handler of the event in
    registration order, each in its own ``try``, and then raise this when
    at least one raised an ``Exception`` (ADR-081 points 1 and 2). The
    post-command path, ``EventBus.dispatch_many``, logs and swallows
    instead: its command has committed.

    ``exceptions`` holds each failed handler's own exception object,
    unchanged, in registration order; behind
    :class:`~pydomain.cqrs.behaviors.EventIdempotencyBehavior` it is the one
    the behavior re-raised after releasing its reservation. ``event_type``
    is the event's class name. The message names the event type and the
    count only, ``<n> handler(s) failed for <EventType>``, and ``repr()``
    shows that message only, never the members (ADR-071, B-195).

    It is an ``ExceptionGroup``: ``except* EventInProgressError`` matches
    one member, and ``split()`` or ``except*`` give an
    ``EventHandlersFailed`` that recounts its members. ``except Exception``
    catches it, and so does ``except CQRSError``.
    """

    event_type: str

    def __new__(cls, event_type: str, failures: Sequence[Exception]) -> Self:
        self = super().__new__(
            cls, f"{len(failures)} handler(s) failed for {event_type}", failures
        )
        self.event_type = event_type
        return self

    def __init__(self, event_type: str, failures: Sequence[Exception]) -> None:
        super().__init__(self.message, failures)

    def derive(  # type: ignore[override]
        self, excs: Sequence[Exception]
    ) -> EventHandlersFailed:
        """Rebuild the group for ``split()`` and ``except*``, recounting."""
        return EventHandlersFailed(self.event_type, excs)

    def __reduce__(self) -> tuple[Any, ...]:
        # BaseException.__reduce__ returns (cls, self.args), which would pass
        # the message as the event type and double it on pickle and copy.
        return (type(self), (self.event_type, list(self.exceptions)), self.__dict__)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.message!r})"


class IdempotentCommandIgnored(CQRSError):
    """Raised when a duplicate command is detected and ignored.

    Carries the duplicate command_id for logging and diagnostics.
    """

    def __init__(self, command_id: UUID, message: str | None = None) -> None:
        self.command_id = command_id
        super().__init__(
            message
            if message is not None
            else f"Command {command_id} ignored (duplicate)"
        )
