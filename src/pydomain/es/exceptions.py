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


class StaleSnapshotError(DomainError):
    """Raised when a snapshot's schema version does not match the aggregate's
    expected version.

    Carries diagnostic information to help users identify which aggregate
    and what version mismatch caused the problem.
    """

    def __init__(
        self,
        aggregate_id: str,
        snapshot_version: int,
        expected_version: int,
    ) -> None:
        self.aggregate_id = aggregate_id
        self.snapshot_schema_version = snapshot_version
        self.expected_schema_version = expected_version
        super().__init__(
            f"Stale snapshot for aggregate {aggregate_id!r}: "
            f"snapshot schema_version={snapshot_version}, "
            f"expected schema_version={expected_version}."
        )
