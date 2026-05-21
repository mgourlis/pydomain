from __future__ import annotations

from pydomain.ddd.exceptions import DomainError


class StreamNotFoundError(DomainError):
    """Raised when an event stream does not exist."""

    def __init__(self, aggregate_id: str) -> None:
        self.aggregate_id = aggregate_id
        super().__init__(f"Event stream for aggregate {aggregate_id!r} not found.")


class UpcastError(DomainError):
    """Raised when an upcaster fails to transform an event."""


class DuplicateCommandError(DomainError):
    """Raised when a command has already been processed for an aggregate."""

    def __init__(self, aggregate_id: str, command_id: str) -> None:
        self.aggregate_id = aggregate_id
        self.command_id = command_id
        super().__init__(
            f"Command {command_id!r} already processed for aggregate {aggregate_id!r}."
        )
