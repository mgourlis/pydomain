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
    This includes a retry after a failed ``commit()``. Roll back, or enter the
    unit of work again, to start a new scope.
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
