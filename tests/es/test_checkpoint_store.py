"""Tests for the CheckpointStore protocol and FakeCheckpointStore.

Covers the runtime-checkable Protocol conformance, load/save semantics
for the in-memory fake, including the default-0 behaviour for unknown
subscription IDs, idempotent re-saves, and subscription isolation.
"""

from __future__ import annotations

import pytest

from pydomain.es.checkpoint_store import CheckpointStore
from pydomain.testing.fake_checkpoint_store import FakeCheckpointStore

# ===================================================================
# CheckpointStore Protocol Conformance
# ===================================================================


class TestCheckpointStoreProtocol:
    """``isinstance`` checks for the ``CheckpointStore`` runtime-checkable
    protocol."""

    def test_fake_checkpoint_store_passes_isinstance(self) -> None:
        """``isinstance(FakeCheckpointStore(), CheckpointStore)`` returns
        ``True``."""
        store = FakeCheckpointStore()
        assert isinstance(store, CheckpointStore)

    def test_protocol_methods_are_async(self) -> None:
        """Both protocol methods are ``async def`` (confirmed via
        ``inspect.iscoroutinefunction``)."""
        import inspect

        store = FakeCheckpointStore()

        assert inspect.iscoroutinefunction(store.load)
        assert inspect.iscoroutinefunction(store.save)


# ===================================================================
# FakeCheckpointStore -- Load
# ===================================================================


class TestFakeCheckpointStoreLoad:
    """``load()`` -- retrieving saved checkpoints from the in-memory
    store."""

    @pytest.mark.anyio
    async def test_returns_zero_for_unknown_subscription(self) -> None:
        """Loading a subscription_id that has never been saved returns
        ``0``."""
        store = FakeCheckpointStore()
        result = await store.load("unknown")
        assert result == 0

    @pytest.mark.anyio
    async def test_returns_saved_value(self) -> None:
        """After ``save(id, checkpoint)``, ``load(id)`` returns the exact
        checkpoint that was saved."""
        store = FakeCheckpointStore()
        await store.save("sub-1", 42)

        result = await store.load("sub-1")
        assert result == 42

    @pytest.mark.anyio
    async def test_returns_updated_value_after_re_save(self) -> None:
        """Saving the same subscription_id twice returns the value from the
        latest save."""
        store = FakeCheckpointStore()
        await store.save("sub-1", 5)
        await store.save("sub-1", 10)

        result = await store.load("sub-1")
        assert result == 10

    @pytest.mark.anyio
    async def test_zero_is_valid_checkpoint(self) -> None:
        """Checkpoint ``0`` is a valid value and is persisted distinctly
        from a never-saved subscription."""
        store = FakeCheckpointStore()
        await store.save("sub-zero", 0)

        result = await store.load("sub-zero")
        assert result == 0

        # An unknown subscription also returns 0
        unknown = await store.load("never-saved")
        assert unknown == 0

        # But they are distinct entries in the store
        assert "sub-zero" in store._store
        assert "never-saved" not in store._store

    @pytest.mark.anyio
    async def test_different_subscriptions_independent(self) -> None:
        """Different subscription IDs have independent checkpoint values."""
        store = FakeCheckpointStore()
        await store.save("sub-a", 1)
        await store.save("sub-b", 99)

        assert await store.load("sub-a") == 1
        assert await store.load("sub-b") == 99


# ===================================================================
# FakeCheckpointStore -- Save
# ===================================================================


class TestFakeCheckpointStoreSave:
    """``save()`` -- persisting checkpoints in the in-memory store."""

    @pytest.mark.anyio
    async def test_persists_to_dict(self) -> None:
        """After ``save(id, cp)``, the raw ``_store`` dict contains the
        expected mapping."""
        store = FakeCheckpointStore()
        await store.save("sub-1", 7)

        assert store._store["sub-1"] == 7

    @pytest.mark.anyio
    async def test_zero_is_valid_checkpoint(self) -> None:
        """Checkpoint ``0`` is a valid persisted value (not treated as
        absent)."""
        store = FakeCheckpointStore()
        await store.save("sub-1", 0)

        assert await store.load("sub-1") == 0
        assert store._store["sub-1"] == 0

    @pytest.mark.anyio
    async def test_multiple_subscriptions_independent(self) -> None:
        """Saving for different subscription IDs does not interfere."""
        store = FakeCheckpointStore()
        await store.save("sub-a", 1)
        await store.save("sub-b", 99)

        assert await store.load("sub-a") == 1
        assert await store.load("sub-b") == 99

    @pytest.mark.anyio
    async def test_save_overwrites_existing(self) -> None:
        """Saving again with the same subscription ID replaces the previous
        checkpoint."""
        store = FakeCheckpointStore()
        await store.save("metrics", 5)
        await store.save("metrics", 10)

        assert await store.load("metrics") == 10
        assert len(store._store) == 1  # No duplicate entries

    @pytest.mark.anyio
    async def test_save_with_large_checkpoint_number(self) -> None:
        """Very large checkpoint values are stored and retrieved correctly."""
        store = FakeCheckpointStore()
        large = 2**63 - 1

        await store.save("large", large)

        assert await store.load("large") == large
