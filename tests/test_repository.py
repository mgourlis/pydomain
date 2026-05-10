from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from pydomain.ddd import AggregateRoot
from pydomain.ddd.exceptions import (
    AggregateNotFoundError,
    ConcurrencyError,
    RepositoryError,
)
from pydomain.ddd.repository import FakeRepository

# ---------------------------------------------------------------------------
# Module-level AggregateRoot subclass for testing
# ---------------------------------------------------------------------------


class InventoryItem(AggregateRoot[UUID]):
    name: str
    quantity: int = 0


# ===================================================================
# Adding Aggregates
# ===================================================================


class TestAdd:
    @pytest.mark.anyio
    async def test_add_stores_aggregate(self) -> None:
        repo = FakeRepository[InventoryItem, UUID]()
        item = InventoryItem(id=uuid4(), name="Widget")
        await repo.add(item)
        retrieved = await repo.get_by_id(item.id)
        assert retrieved == item

    @pytest.mark.anyio
    async def test_add_tracks_in_seen(self) -> None:
        repo = FakeRepository[InventoryItem, UUID]()
        item = InventoryItem(id=uuid4(), name="Widget")
        await repo.add(item)
        assert item in repo._seen

    @pytest.mark.anyio
    async def test_add_multiple_aggregates(self) -> None:
        repo = FakeRepository[InventoryItem, UUID]()
        item_a = InventoryItem(id=uuid4(), name="Widget")
        item_b = InventoryItem(id=uuid4(), name="Gadget")
        await repo.add(item_a)
        await repo.add(item_b)
        assert await repo.get_by_id(item_a.id) == item_a
        assert await repo.get_by_id(item_b.id) == item_b

    @pytest.mark.anyio
    async def test_add_existing_raises_error(self) -> None:
        uid = uuid4()
        original = InventoryItem(id=uid, name="Widget")
        repo = FakeRepository[InventoryItem, UUID]()
        await repo.add(original)
        duplicate = InventoryItem(id=uid, name="Widget-v2")
        with pytest.raises(RepositoryError, match="already exists"):
            await repo.add(duplicate)


# ===================================================================
# Getting by ID
# ===================================================================


class TestGetById:
    @pytest.mark.anyio
    async def test_get_by_id_returns_none_when_not_found(self) -> None:
        repo = FakeRepository[InventoryItem, UUID]()
        result = await repo.get_by_id(uuid4())
        assert result is None

    @pytest.mark.anyio
    async def test_get_by_id_returns_aggregate(self) -> None:
        item = InventoryItem(id=uuid4(), name="Gadget")
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item])
        retrieved = await repo.get_by_id(item.id)
        assert retrieved == item
        assert retrieved.name == "Gadget"

    @pytest.mark.anyio
    async def test_get_by_id_with_different_id_types(
        self,
    ) -> None:
        class IntItem(AggregateRoot[int]):
            label: str = ""

        repo = FakeRepository[IntItem, int]()
        item = IntItem(id=42, label="answer")
        await repo.add(item)
        retrieved = await repo.get_by_id(42)
        assert retrieved == item
        assert await repo.get_by_id(99) is None

    @pytest.mark.anyio
    async def test_get_by_id_returns_same_object_reference(
        self,
    ) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item])
        retrieved = await repo.get_by_id(item.id)
        assert retrieved is item
        retrieved.name = "Mutated"
        assert item.name == "Mutated"


# ===================================================================
# Updating with Optimistic Concurrency
# ===================================================================


class TestUpdate:
    @pytest.mark.anyio
    async def test_update_persists_changes(self) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item])
        item.quantity = 10
        await repo.update(item)
        retrieved = await repo.get_by_id(item.id)
        assert retrieved is not None
        assert retrieved.quantity == 10

    @pytest.mark.anyio
    async def test_update_increments_version(self) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item])
        assert item.version == 0
        item.quantity = 10
        await repo.update(item)
        assert item.version == 1

    @pytest.mark.anyio
    async def test_update_increments_version_multiple_times(self) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item])
        for _ in range(3):
            item.quantity += 1
            await repo.update(item)
        assert item.version == 3

    @pytest.mark.anyio
    async def test_update_with_mismatched_version_raises_concurrency_error(
        self,
    ) -> None:
        item = InventoryItem(id=uuid4(), name="Widget", version=1)
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item])

        # Simulate a concurrent update that bumps the stored version
        item.quantity = 5
        await repo.update(item)  # stored version becomes 2

        # Now try to update with a stale version
        stale = InventoryItem(id=item.id, name="Widget", version=1)
        stale.quantity = 10
        with pytest.raises(ConcurrencyError):
            await repo.update(stale)

    @pytest.mark.anyio
    async def test_update_aggregate_not_found_raises_error(self) -> None:
        repo = FakeRepository[InventoryItem, UUID]()
        item = InventoryItem(id=uuid4(), name="Ghost")
        with pytest.raises(AggregateNotFoundError):
            await repo.update(item)

    @pytest.mark.anyio
    async def test_update_tracks_in_seen(self) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item])
        item.quantity = 5
        await repo.update(item)
        assert item in repo._seen

    @pytest.mark.anyio
    async def test_update_verifies_version_before_increment(self) -> None:
        """Ensure the version check uses the expected version (on the
        aggregate) against the stored version."""
        item = InventoryItem(id=uuid4(), name="Widget")
        repo = FakeRepository[InventoryItem, UUID]()
        await repo.add(item)
        assert item.version == 0

        # Update succeeds with matching version
        await repo.update(item)
        assert item.version == 1

        # Same aggregate again — version 1 matches stored version 1
        await repo.update(item)
        assert item.version == 2

    @pytest.mark.anyio
    async def test_update_with_higher_version_raises_concurrency_error(
        self,
    ) -> None:
        uid = uuid4()
        item = InventoryItem(id=uid, name="Widget", version=2)
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item])
        stale = InventoryItem(id=uid, name="Widget", version=5)
        with pytest.raises(ConcurrencyError):
            await repo.update(stale)

    @pytest.mark.anyio
    async def test_update_after_delete_raises_not_found(self) -> None:
        uid = uuid4()
        item = InventoryItem(id=uid, name="Widget")
        repo = FakeRepository[InventoryItem, UUID]()
        await repo.add(item)
        await repo.delete(uid)
        with pytest.raises(AggregateNotFoundError):
            await repo.update(item)

    @pytest.mark.anyio
    async def test_update_after_track_only_raises_not_found(self) -> None:
        uid = uuid4()
        item = InventoryItem(id=uid, name="Widget")
        repo = FakeRepository[InventoryItem, UUID]()
        await repo.track(item)
        with pytest.raises(AggregateNotFoundError):
            await repo.update(item)

    @pytest.mark.anyio
    async def test_concurrent_first_writer_wins_with_version_zero(
        self,
    ) -> None:
        uid = uuid4()
        item = InventoryItem(id=uid, name="Widget")
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item])
        worker_a = InventoryItem(id=uid, name="Widget")
        worker_b = InventoryItem(id=uid, name="Widget")
        assert worker_a.version == 0
        assert worker_b.version == 0
        worker_a.quantity = 5
        await repo.update(worker_a)
        worker_b.quantity = 10
        with pytest.raises(ConcurrencyError):
            await repo.update(worker_b)


# ===================================================================
# Deleting
# ===================================================================


class TestDelete:
    @pytest.mark.anyio
    async def test_delete_removes_aggregate(self) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item])
        await repo.delete(item.id)
        assert await repo.get_by_id(item.id) is None

    @pytest.mark.anyio
    async def test_delete_non_existent_is_silent(self) -> None:
        repo = FakeRepository[InventoryItem, UUID]()
        # Should not raise
        await repo.delete(uuid4())

    @pytest.mark.anyio
    async def test_delete_only_removes_target(self) -> None:
        item_a = InventoryItem(id=uuid4(), name="Widget")
        item_b = InventoryItem(id=uuid4(), name="Gadget")
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item_a, item_b])
        await repo.delete(item_a.id)
        assert await repo.get_by_id(item_a.id) is None
        assert await repo.get_by_id(item_b.id) == item_b

    @pytest.mark.anyio
    async def test_delete_twice_is_silent(self) -> None:
        uid = uuid4()
        item = InventoryItem(id=uid, name="Widget")
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item])
        await repo.delete(uid)
        await repo.delete(uid)

    @pytest.mark.anyio
    async def test_delete_does_not_remove_from_seen(self) -> None:
        uid = uuid4()
        item = InventoryItem(id=uid, name="Widget")
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item])
        await repo.track(item)
        assert item in repo._seen
        await repo.delete(uid)
        assert item in repo._seen


# ===================================================================
# Seeded Repository
# ===================================================================


class TestSeededRepository:
    @pytest.mark.anyio
    async def test_seeded_with_list(self) -> None:
        items = [
            InventoryItem(id=uuid4(), name="Widget"),
            InventoryItem(id=uuid4(), name="Gadget"),
        ]
        repo = FakeRepository[InventoryItem, UUID](aggregates=items)
        assert await repo.get_by_id(items[0].id) == items[0]
        assert await repo.get_by_id(items[1].id) == items[1]

    @pytest.mark.anyio
    async def test_seeded_with_empty_list(self) -> None:
        repo = FakeRepository[InventoryItem, UUID](aggregates=[])
        assert await repo.get_by_id(uuid4()) is None

    @pytest.mark.anyio
    async def test_seeded_with_none_is_empty(self) -> None:
        repo = FakeRepository[InventoryItem, UUID](aggregates=None)
        assert await repo.get_by_id(uuid4()) is None


# ===================================================================
# _seen Tracking
# ===================================================================


class TestSeenTracking:
    @pytest.mark.anyio
    async def test_track_adds_to_seen(self) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        repo = FakeRepository[InventoryItem, UUID]()
        await repo.track(item)
        assert item in repo._seen

    @pytest.mark.anyio
    async def test_track_does_not_add_to_store(self) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        repo = FakeRepository[InventoryItem, UUID]()
        await repo.track(item)
        assert item in repo._seen
        assert await repo.get_by_id(item.id) is None

    @pytest.mark.anyio
    async def test_track_multiple_aggregates(self) -> None:
        item_a = InventoryItem(id=uuid4(), name="A")
        item_b = InventoryItem(id=uuid4(), name="B")
        repo = FakeRepository[InventoryItem, UUID]()
        await repo.track(item_a)
        await repo.track(item_b)
        assert item_a in repo._seen
        assert item_b in repo._seen

    @pytest.mark.anyio
    async def test_seen_does_not_include_unseen_aggregates(self) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        repo = FakeRepository[InventoryItem, UUID]()
        assert item not in repo._seen

    @pytest.mark.anyio
    async def test_seeded_aggregates_are_not_automatically_seen(self) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item])
        assert item not in repo._seen

    @pytest.mark.anyio
    async def test_seen_deduplicates_by_id(self) -> None:
        uid = uuid4()
        item_a = InventoryItem(id=uid, name="Widget")
        item_b = InventoryItem(id=uid, name="Widget")
        repo = FakeRepository[InventoryItem, UUID]()
        await repo.track(item_a)
        await repo.track(item_b)
        assert len(repo._seen) == 1

    @pytest.mark.anyio
    async def test_track_idempotent(self) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        repo = FakeRepository[InventoryItem, UUID]()
        await repo.track(item)
        assert len(repo._seen) == 1
        await repo.track(item)
        assert len(repo._seen) == 1


# ===================================================================
# Edge Cases
# ===================================================================


class TestEdgeCases:
    @pytest.mark.anyio
    async def test_re_add_after_delete_works(self) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item])
        await repo.delete(item.id)
        await repo.add(item)
        assert await repo.get_by_id(item.id) == item

    @pytest.mark.anyio
    async def test_update_after_re_add(self) -> None:
        """Re-adding an aggregate resets its stored state."""
        uid = uuid4()
        item = InventoryItem(id=uid, name="Widget", version=3)
        repo = FakeRepository[InventoryItem, UUID]()
        await repo.add(item)
        # The stored version is 3 since add does not modify version
        item.quantity = 5
        await repo.update(item)  # version 3 matches → OK, version → 4
        assert item.version == 4

    @pytest.mark.anyio
    async def test_add_then_update_same_aggregate(self) -> None:
        uid = uuid4()
        item = InventoryItem(id=uid, name="Widget")
        repo = FakeRepository[InventoryItem, UUID]()
        await repo.add(item)  # stored version = 0
        item.quantity = 5
        await repo.update(item)  # version 0 matches, version → 1
        assert item.version == 1
        retrieved = await repo.get_by_id(uid)
        assert retrieved is not None
        assert retrieved.quantity == 5
        assert retrieved.version == 1
