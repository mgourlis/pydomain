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
