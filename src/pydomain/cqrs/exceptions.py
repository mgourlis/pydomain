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
