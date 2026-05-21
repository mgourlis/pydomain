from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from pydomain.ddd import AggregateRoot, DomainEvent
from pydomain.ddd.exceptions import ConcurrencyError
from pydomain.testing import FakeRepository

# ---------------------------------------------------------------------------
# Module-level AggregateRoot subclass for testing
# ---------------------------------------------------------------------------


class InventoryItem(AggregateRoot[UUID]):
    name: str
    quantity: int = 0


class ItemRenamed(DomainEvent):
    new_name: str


# ===================================================================
# Saving Aggregates (insert + update unified)
# ===================================================================


class TestSave:
    @pytest.mark.anyio
    async def test_save_new_aggregate_stores_it(self) -> None:
        repo = FakeRepository[InventoryItem, UUID]()
        item = InventoryItem(id=uuid4(), name="Widget")
        await repo.save(item)
        retrieved = await repo.get_by_id(item.id)
        assert retrieved == item

    @pytest.mark.anyio
    async def test_save_multiple_new_aggregates(self) -> None:
        repo = FakeRepository[InventoryItem, UUID]()
        item_a = InventoryItem(id=uuid4(), name="Widget")
        item_b = InventoryItem(id=uuid4(), name="Gadget")
        await repo.save(item_a)
        await repo.save(item_b)
        assert await repo.get_by_id(item_a.id) == item_a
        assert await repo.get_by_id(item_b.id) == item_b

    @pytest.mark.anyio
    async def test_save_existing_aggregate_updates(self) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item])
        item.quantity = 10
        await repo.save(item)
        retrieved = await repo.get_by_id(item.id)
        assert retrieved is not None
        assert retrieved.quantity == 10

    @pytest.mark.anyio
    async def test_save_existing_increments_version(self) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item])
        assert item.version == 0
        item.quantity = 10
        await repo.save(item)
        assert item.version == 1

    @pytest.mark.anyio
    async def test_save_existing_increments_version_multiple_times(
        self,
    ) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item])
        for _ in range(3):
            item.quantity += 1
            await repo.save(item)
        assert item.version == 3

    @pytest.mark.anyio
    async def test_save_new_aggregate_does_not_increment_version(
        self,
    ) -> None:
        """INSERT (new aggregate) keeps version at 0."""
        item = InventoryItem(id=uuid4(), name="Widget")
        repo = FakeRepository[InventoryItem, UUID]()
        assert item.version == 0
        await repo.save(item)
        assert item.version == 0

    @pytest.mark.anyio
    async def test_save_with_mismatched_version_raises_concurrency_error(
        self,
    ) -> None:
        item = InventoryItem(id=uuid4(), name="Widget", version=1)
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item])

        # First save bumps stored version to 2
        item.quantity = 5
        await repo.save(item)

        # Now try to save a stale copy at version 1
        stale = InventoryItem(id=item.id, name="Widget", version=1)
        stale.quantity = 10
        with pytest.raises(ConcurrencyError):
            await repo.save(stale)

    @pytest.mark.anyio
    async def test_save_with_higher_version_raises_concurrency_error(
        self,
    ) -> None:
        uid = uuid4()
        item = InventoryItem(id=uid, name="Widget", version=2)
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item])
        stale = InventoryItem(id=uid, name="Widget", version=5)
        with pytest.raises(ConcurrencyError):
            await repo.save(stale)

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
        await repo.save(worker_a)
        worker_b.quantity = 10
        with pytest.raises(ConcurrencyError):
            await repo.save(worker_b)

    @pytest.mark.anyio
    async def test_save_verifies_version_before_increment(self) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        repo = FakeRepository[InventoryItem, UUID]()
        await repo.save(item)
        assert item.version == 0

        # First update: version 0 matches stored version 0
        await repo.save(item)
        assert item.version == 1

        # Second update: version 1 matches stored version 1
        await repo.save(item)
        assert item.version == 2

    @pytest.mark.anyio
    async def test_save_after_delete_treated_as_insert(self) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item])
        await repo.delete(item.id)
        # Re-save treats it as a new aggregate (no version increment)
        await repo.save(item)
        assert await repo.get_by_id(item.id) == item
        assert item.version == 0


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
        await repo.save(item)
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
# Event Collection via pull_events()
# ===================================================================


class TestPullEvents:
    @pytest.mark.anyio
    async def test_pull_events_returns_empty_when_no_events(self) -> None:
        repo = FakeRepository[InventoryItem, UUID]()
        assert repo.pull_events() == []

    @pytest.mark.anyio
    async def test_save_drains_events_from_aggregate(self) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        event = ItemRenamed(new_name="Gadget")
        item._add_event(event)
        repo = FakeRepository[InventoryItem, UUID]()
        await repo.save(item)
        # Aggregate's event buffer should be drained
        assert item.pull_events() == []

    @pytest.mark.anyio
    async def test_pull_events_returns_events_after_save(self) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        event = ItemRenamed(new_name="Gadget")
        item._add_event(event)
        repo = FakeRepository[InventoryItem, UUID]()
        await repo.save(item)
        events = repo.pull_events()
        assert len(events) == 1
        assert isinstance(events[0], ItemRenamed)
        assert events[0].new_name == "Gadget"

    @pytest.mark.anyio
    async def test_pull_events_drains_buffer(self) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        item._add_event(ItemRenamed(new_name="Gadget"))
        repo = FakeRepository[InventoryItem, UUID]()
        await repo.save(item)
        assert len(repo.pull_events()) == 1
        assert repo.pull_events() == []

    @pytest.mark.anyio
    async def test_multiple_saves_accumulate_events(self) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        repo = FakeRepository[InventoryItem, UUID](aggregates=[item])
        item._add_event(ItemRenamed(new_name="A"))
        await repo.save(item)
        item._add_event(ItemRenamed(new_name="B"))
        await repo.save(item)
        events = repo.pull_events()
        assert len(events) == 2

    @pytest.mark.anyio
    async def test_multiple_aggregates_events_collected(self) -> None:
        item_a = InventoryItem(id=uuid4(), name="Widget")
        item_b = InventoryItem(id=uuid4(), name="Gadget")
        item_a._add_event(ItemRenamed(new_name="A1"))
        item_b._add_event(ItemRenamed(new_name="B1"))
        repo = FakeRepository[InventoryItem, UUID]()
        await repo.save(item_a)
        await repo.save(item_b)
        events = repo.pull_events()
        assert len(events) == 2

    @pytest.mark.anyio
    async def test_save_with_no_events_collects_nothing(self) -> None:
        item = InventoryItem(id=uuid4(), name="Widget")
        repo = FakeRepository[InventoryItem, UUID]()
        await repo.save(item)
        assert repo.pull_events() == []
