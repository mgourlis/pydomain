from __future__ import annotations

from pydomain.es.checkpoint_store import CheckpointStore


class FakeCheckpointStore(CheckpointStore):
    """In-memory checkpoint store for testing.

    Stores checkpoints in a plain ``dict`` keyed by subscription ID.
    No serialization round-trip is performed.
    """

    def __init__(self) -> None:
        self._store: dict[str, int] = {}

    async def load(self, subscription_id: str) -> int:
        """Load checkpoint for a subscription.

        Parameters
        ----------
        subscription_id:
            The unique identity of the subscription.

        Returns
        -------
        int
            The last processed global event version, or ``0`` if none saved.
        """
        return self._store.get(subscription_id, 0)

    async def save(self, subscription_id: str, checkpoint: int) -> None:
        """Persist the checkpoint for a subscription.

        Parameters
        ----------
        subscription_id:
            The unique identity of the subscription.
        checkpoint:
            The global event version to persist.
        """
        self._store[subscription_id] = checkpoint
