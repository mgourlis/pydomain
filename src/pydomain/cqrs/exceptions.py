from __future__ import annotations

from typing import Any
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
    The message carries no content (ADR-071).
    """


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
