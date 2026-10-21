"""Contract suite for the ``CheckpointStore`` Protocol (ADR-052)."""

from __future__ import annotations

import pytest

from pydomain.es.checkpoint_store import CheckpointStore

__all__ = ["CheckpointStoreContract"]


class CheckpointStoreContract:
    """Cases every :class:`~pydomain.es.checkpoint_store.CheckpointStore` passes.

    Override the ``checkpoint_store`` fixture to return a fresh, empty store
    for each test.
    """

    @pytest.fixture
    def checkpoint_store(self) -> CheckpointStore:
        """Return a fresh, empty store. Override in your subclass."""
        raise NotImplementedError(
            "Override the `checkpoint_store` fixture to return your adapter."
        )

    def test_satisfies_protocol(self, checkpoint_store: CheckpointStore) -> None:
        """The store is a ``CheckpointStore``."""
        assert isinstance(checkpoint_store, CheckpointStore)

    @pytest.mark.anyio
    async def test_load_returns_zero_for_unknown_subscription(
        self, checkpoint_store: CheckpointStore
    ) -> None:
        """A subscription with no saved checkpoint loads as ``0``."""
        assert await checkpoint_store.load("contract-unknown") == 0

    @pytest.mark.anyio
    async def test_load_returns_saved_checkpoint(
        self, checkpoint_store: CheckpointStore
    ) -> None:
        """``load`` returns the checkpoint ``save`` persisted."""
        await checkpoint_store.save("contract-orders", 42)

        assert await checkpoint_store.load("contract-orders") == 42

    @pytest.mark.anyio
    async def test_save_replaces_previous_checkpoint(
        self, checkpoint_store: CheckpointStore
    ) -> None:
        """A second ``save`` for a subscription replaces the first."""
        await checkpoint_store.save("contract-orders", 10)
        await checkpoint_store.save("contract-orders", 20)

        assert await checkpoint_store.load("contract-orders") == 20

    @pytest.mark.anyio
    async def test_checkpoints_are_kept_per_subscription(
        self, checkpoint_store: CheckpointStore
    ) -> None:
        """Each subscription has its own checkpoint."""
        await checkpoint_store.save("contract-orders", 100)
        await checkpoint_store.save("contract-invoices", 200)

        assert await checkpoint_store.load("contract-orders") == 100
        assert await checkpoint_store.load("contract-invoices") == 200
