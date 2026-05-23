from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class Snapshot(BaseModel):
    """Snapshot of an aggregate at a given version.

    Snapshots capture the full state of an aggregate (via ``model_dump()``)
    at a specific version, enabling fast rebuild without replaying the
    entire event stream.

    The ``schema_version`` field tracks the aggregate's schema at the time
    the snapshot was taken. When the aggregate's fields change, the schema
    version should be bumped. A :class:`SnapshotSchemaPolicy` can then
    detect stale snapshots and force a full replay.
    """

    aggregate_id: str
    version: int
    state: dict[str, Any]
    schema_version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


@runtime_checkable
class SnapshotPolicy(Protocol):
    """Decides whether a snapshot should be taken for an aggregate."""

    def should_snapshot(
        self,
        aggregate_type: str,
        aggregate_id: str,
        current_version: int,
        pending_event_count: int,
    ) -> bool:
        """Return True if a snapshot should be taken now.

        Parameters
        ----------
        aggregate_type:
            The type discriminator for the aggregate (e.g. ``"Order"``).
        aggregate_id:
            The aggregate identity.
        current_version:
            The aggregate's current version number.
        pending_event_count:
            Number of events pending since the last snapshot.

        Returns
        -------
        bool
            ``True`` if a snapshot should be taken now.
        """
        ...


class SnapshotThresholdPolicy(SnapshotPolicy):
    """Snapshot every N events (when ``current_version % threshold == 0``).

    When *threshold* is ``0``, uses ``pending_event_count > 0`` instead,
    meaning every flush triggers a snapshot.

    Parameters
    ----------
    threshold:
        Snapshot every *threshold* events. Defaults to ``10``.
    """

    def __init__(self, threshold: int = 10) -> None:
        if threshold < 0:
            raise ValueError("threshold must be >= 0")
        self._threshold = threshold

    def should_snapshot(
        self,
        aggregate_type: str,
        aggregate_id: str,
        current_version: int,
        pending_event_count: int,
    ) -> bool:
        if self._threshold == 0:
            return pending_event_count > 0
        return current_version % self._threshold == 0


@runtime_checkable
class SnapshotSchemaPolicy(Protocol):
    """Decides whether a snapshot is compatible with the current aggregate
    schema.

    When the aggregate's fields change (rename, type change, removal),
    previously saved snapshots may be incompatible. This policy is
    evaluated during ``get_by_id()`` before using a snapshot for hydration.
    If the policy rejects the snapshot, the repository falls back to
    full event replay.
    """

    def should_use_snapshot(
        self,
        snapshot: Snapshot,
        expected_schema_version: int,
    ) -> bool:
        """Return ``True`` if the snapshot is compatible with the current
        aggregate schema.

        Parameters
        ----------
        snapshot:
            The loaded snapshot.
        expected_schema_version:
            The aggregate's current ``_snapshot_schema_version``.

        Returns
        -------
        bool
            ``True`` if the snapshot should be used for hydration.
        """
        ...


class RejectStaleSnapshotPolicy(SnapshotSchemaPolicy):
    """Reject snapshots whose ``schema_version`` does not match the
    aggregate's expected version.

    This is the simplest schema policy: if versions differ, the snapshot
    is considered stale and the repository falls back to full event replay.
    """

    def should_use_snapshot(
        self,
        snapshot: Snapshot,
        expected_schema_version: int,
    ) -> bool:
        return snapshot.schema_version == expected_schema_version


@runtime_checkable
class SnapshotStore(Protocol):
    """Snapshot store protocol for event-sourced aggregates."""

    async def save(self, aggregate_type: str, snapshot: Snapshot) -> None:
        """Persist a snapshot for the given aggregate type.

        Parameters
        ----------
        aggregate_type:
            The type discriminator for the aggregate (e.g. ``"Order"``).
        snapshot:
            The snapshot to persist.
        """
        ...

    async def get(
        self,
        aggregate_type: str,
        aggregate_id: str,
    ) -> Snapshot | None:
        """Retrieve the latest snapshot for an aggregate.

        Parameters
        ----------
        aggregate_type:
            The type discriminator for the aggregate.
        aggregate_id:
            The aggregate identity.

        Returns
        -------
        Snapshot | None
            The latest snapshot if one exists, otherwise ``None``.
        """
        ...
