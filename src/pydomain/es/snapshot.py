from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class Snapshot(BaseModel):
    """Snapshot of an aggregate at a given version.

    Snapshots capture the full state of an aggregate (via ``model_dump()``)
    at a specific version, enabling fast rebuild without replaying the
    entire event stream.
    """

    aggregate_id: str
    version: int
    state: dict
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
