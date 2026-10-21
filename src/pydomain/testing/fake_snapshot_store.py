from __future__ import annotations

from pydomain.es.snapshot import Snapshot, SnapshotStore


class FakeSnapshotStore(SnapshotStore):
    """In-memory SnapshotStore for testing.

    Stores snapshots keyed by ``(aggregate_type, aggregate_id)``.
    No serialization round-trip is performed, but ``save`` stores a deep
    copy and ``get`` returns a deep copy, so changing a snapshot after
    saving it, or one that ``get`` returned, never changes what the store
    holds (the ``SnapshotStore`` contract, ADR-075).
    """

    def __init__(self) -> None:
        self._snapshots: dict[tuple[str, str], Snapshot] = {}

    async def save(self, aggregate_type: str, snapshot: Snapshot) -> None:
        """Persist a snapshot for the given aggregate type.

        Parameters
        ----------
        aggregate_type:
            The type discriminator for the aggregate (e.g. ``"Order"``).
        snapshot:
            The snapshot to persist.
        """
        self._snapshots[(aggregate_type, snapshot.aggregate_id)] = snapshot.model_copy(
            deep=True
        )

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
            A copy of the latest snapshot if one exists, otherwise ``None``.
        """
        snapshot = self._snapshots.get((aggregate_type, aggregate_id))
        return None if snapshot is None else snapshot.model_copy(deep=True)
