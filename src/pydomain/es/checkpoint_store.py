from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CheckpointStore(Protocol):
    """Protocol for persisting subscription checkpoints.

    Tracks the last processed global event version for each subscription.
    """

    async def load(self, subscription_id: str) -> int:
        """Load checkpoint for a subscription.

        Returns the last processed global event version, or ``0`` if none
        saved.

        Parameters
        ----------
        subscription_id:
            The unique identity of the subscription.

        Returns
        -------
        int
            The last processed global event version, or ``0``.
        """
        ...

    async def save(self, subscription_id: str, checkpoint: int) -> None:
        """Persist the checkpoint for a subscription.

        Parameters
        ----------
        subscription_id:
            The unique identity of the subscription.
        checkpoint:
            The global event version to persist.
        """
        ...
