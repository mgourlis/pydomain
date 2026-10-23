from __future__ import annotations

from typing import Any

from pydomain.ddd.exceptions import (
    DomainError,
    _restore_exception,  # pyright: ignore[reportPrivateUsage]
)

# Each exception below takes its own constructor arguments, but
# BaseException.__reduce__ passes only ``self.args`` (the message) back to
# __init__, so pickle and copy doubled a message or raised TypeError. Each
# ``__reduce__`` rebuilds it with ``_restore_exception``, which never calls
# ``__init__`` (B-210).


class StreamNotFoundError(DomainError):
    """Raised when an event stream does not exist."""

    def __init__(self, aggregate_id: str) -> None:
        self.aggregate_id = aggregate_id
        super().__init__(f"Event stream for aggregate {aggregate_id!r} not found.")

    def __reduce__(self) -> tuple[Any, ...]:
        return (_restore_exception, (type(self), self.args, self.__dict__))


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

    def __reduce__(self) -> tuple[Any, ...]:
        return (_restore_exception, (type(self), self.args, self.__dict__))


class SnapshotRestoreError(DomainError):
    """Raised when a snapshot's state no longer validates into the aggregate.

    ``EventSourcedRepository.get_by_id()`` restores a snapshot with
    ``model_validate`` (ADR-075). When the stored state does not fit the
    aggregate class (a changed type, a missing required field, a removed
    field's key under ``extra="forbid"``), it raises this error instead of a
    full replay. Any other ``Exception`` raised while the state is
    validated, by the aggregate's validators or ``model_post_init``, is
    reported the same way, by its type name only (B-67). The message names
    the failing locations, which include dict keys (ADR-071), and their
    error codes, never a value; the error has no cause and no context, so
    no stored value reaches a traceback (B-55). A class definition error
    (an aggregate class Pydantic cannot build) also surfaces here, by type
    name only; construct the class directly to see the real message. Bump
    ``_snapshot_schema_version`` and configure ``RejectStaleSnapshotPolicy``
    to skip such snapshots.
    """

    def __init__(
        self,
        aggregate_type: str,
        aggregate_id: str,
        snapshot_version: int,
        description: str,
    ) -> None:
        self.aggregate_id = aggregate_id
        self.snapshot_version = snapshot_version
        super().__init__(
            f"Snapshot of {aggregate_type} {aggregate_id!r} at version "
            f"{snapshot_version} could not be restored: {description}"
        )

    def __reduce__(self) -> tuple[Any, ...]:
        return (_restore_exception, (type(self), self.args, self.__dict__))


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

    def __reduce__(self) -> tuple[Any, ...]:
        return (_restore_exception, (type(self), self.args, self.__dict__))
