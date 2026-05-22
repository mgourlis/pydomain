"""Tests for SnapshotSchemaVersion — schema-based snapshot invalidation.

Covers:
- Snapshot model gains a ``schema_version`` field (default 1).
- ``SnapshotSchemaPolicy`` Protocol conformance.
- ``RejectStaleSnapshotPolicy`` rejects snapshots with mismatched schema versions.
- ``EventSourcedRepository.get_by_id()`` skips stale snapshots and falls back
  to full event replay when the policy rejects the snapshot.
- ``_take_snapshot()`` embeds the aggregate's ``_snapshot_schema_version``.
- ``StaleSnapshotError`` is raised when the policy detects a stale snapshot
  (opt-in, not the default path).
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from pydomain.ddd import DomainEvent
from pydomain.es.aggregate import EventSourcedAggregateRoot
from pydomain.es.event_sourced_repository import EventSourcedRepository
from pydomain.es.exceptions import StaleSnapshotError
from pydomain.es.snapshot import (
    RejectStaleSnapshotPolicy,
    Snapshot,
    SnapshotSchemaPolicy,
)
from pydomain.testing.fake_event_store import FakeEventStore
from pydomain.testing.fake_snapshot_store import FakeSnapshotStore

# ---- Test domain objects ----


class ItemAdded(DomainEvent):
    order_id: str
    item_name: str
    price: float


class V1Order(EventSourcedAggregateRoot[str]):
    """Aggregate with schema version 1 (default)."""

    items: list[dict] = []

    def _when(self, event: DomainEvent) -> None:
        if isinstance(event, ItemAdded):
            self.items.append({"name": event.item_name, "price": event.price})
        else:
            raise ValueError(f"Unknown event: {event!r}")


class V2Order(EventSourcedAggregateRoot[str]):
    """Aggregate with schema version 2 — added ``discount`` field."""

    _snapshot_schema_version: ClassVar[int] = 2

    items: list[dict] = []
    discount: float = 0.0

    def _when(self, event: DomainEvent) -> None:
        if isinstance(event, ItemAdded):
            self.items.append({"name": event.item_name, "price": event.price})
        else:
            raise ValueError(f"Unknown event: {event!r}")


# ===================================================================
# Snapshot model — schema_version field
# ===================================================================


class TestSnapshotSchemaVersionField:
    """``Snapshot`` gains a ``schema_version`` field defaulting to ``1``."""

    def test_default_schema_version_is_1(self) -> None:
        snap = Snapshot(aggregate_id="order-1", version=5, state={})
        assert snap.schema_version == 1

    def test_explicit_schema_version(self) -> None:
        snap = Snapshot(aggregate_id="order-1", version=5, state={}, schema_version=3)
        assert snap.schema_version == 3

    def test_model_dump_includes_schema_version(self) -> None:
        snap = Snapshot(aggregate_id="order-1", version=2, state={})
        dumped = snap.model_dump()
        assert "schema_version" in dumped
        assert dumped["schema_version"] == 1

    def test_model_validate_round_trip(self) -> None:
        snap = Snapshot(
            aggregate_id="order-1", version=5, state={"items": []}, schema_version=2
        )
        restored = Snapshot.model_validate(snap.model_dump())
        assert restored.schema_version == 2
        assert restored.aggregate_id == "order-1"


# ===================================================================
# SnapshotSchemaPolicy Protocol
# ===================================================================


class TestSnapshotSchemaPolicyProtocol:
    """``SnapshotSchemaPolicy`` is runtime-checkable."""

    def test_reject_stale_passes_isinstance(self) -> None:
        policy = RejectStaleSnapshotPolicy()
        assert isinstance(policy, SnapshotSchemaPolicy)

    def test_plain_object_does_not_pass_isinstance(self) -> None:
        assert not isinstance(object(), SnapshotSchemaPolicy)


# ===================================================================
# RejectStaleSnapshotPolicy
# ===================================================================


class TestRejectStaleSnapshotPolicy:
    """``RejectStaleSnapshotPolicy`` rejects snapshots with a schema version
    that does not match the expected version."""

    def test_accepts_matching_schema_version(self) -> None:
        policy = RejectStaleSnapshotPolicy()
        snap = Snapshot(aggregate_id="order-1", version=5, state={}, schema_version=1)
        assert policy.should_use_snapshot(snap, expected_schema_version=1) is True

    def test_rejects_higher_schema_version(self) -> None:
        """A snapshot saved by a newer aggregate (higher schema) is rejected."""
        policy = RejectStaleSnapshotPolicy()
        snap = Snapshot(aggregate_id="order-1", version=5, state={}, schema_version=2)
        assert policy.should_use_snapshot(snap, expected_schema_version=1) is False

    def test_rejects_lower_schema_version(self) -> None:
        """A snapshot saved by an older aggregate (lower schema) is rejected."""
        policy = RejectStaleSnapshotPolicy()
        snap = Snapshot(aggregate_id="order-1", version=5, state={}, schema_version=1)
        assert policy.should_use_snapshot(snap, expected_schema_version=2) is False

    def test_accepts_snapshot_with_matching_high_version(self) -> None:
        policy = RejectStaleSnapshotPolicy()
        snap = Snapshot(aggregate_id="order-1", version=5, state={}, schema_version=5)
        assert policy.should_use_snapshot(snap, expected_schema_version=5) is True

    def test_different_instances_are_independent(self) -> None:
        """Each policy instance operates independently."""
        policy_a = RejectStaleSnapshotPolicy()
        policy_b = RejectStaleSnapshotPolicy()

        snap = Snapshot(aggregate_id="order-1", version=3, state={}, schema_version=1)
        # Both reject the same mismatch
        assert policy_a.should_use_snapshot(snap, expected_schema_version=2) is False
        assert policy_b.should_use_snapshot(snap, expected_schema_version=2) is False


# ===================================================================
# _take_snapshot() embeds schema_version
# ===================================================================


class TestTakeSnapshotIncludesSchemaVersion:
    """``_take_snapshot()`` embeds the aggregate's
    ``_snapshot_schema_version`` in the resulting ``Snapshot``."""

    def test_default_aggregate_produces_schema_version_1(self) -> None:
        order = V1Order(id="order-1")
        snap = order._take_snapshot()
        assert snap.schema_version == 1

    def test_custom_schema_version_embedded_in_snapshot(self) -> None:
        order = V2Order(id="order-2")
        snap = order._take_snapshot()
        assert snap.schema_version == 2

    def test_after_events_schema_version_preserved(self) -> None:
        order = V2Order(id="order-3")
        order._apply(ItemAdded(order_id="order-3", item_name="Widget", price=9.99))
        snap = order._take_snapshot()
        assert snap.version == 1
        assert snap.schema_version == 2


# ===================================================================
# EventSourcedRepository — skips stale snapshot, falls back to replay
# ===================================================================


class TestRepositorySkipsStaleSnapshot:
    """When ``snapshot_schema_policy`` is configured, the repository validates
    the snapshot's schema version before using it. A stale snapshot is skipped
    and the aggregate is rebuilt from the full event stream."""

    @pytest.mark.anyio
    async def test_stale_snapshot_skipped_full_replay_used(self) -> None:
        """A v1 snapshot is rejected when the aggregate expects v2.
        The repository falls back to full event replay."""
        event_store = FakeEventStore()
        snapshot_store = FakeSnapshotStore()
        schema_policy = RejectStaleSnapshotPolicy()

        repo: EventSourcedRepository[V2Order, str] = EventSourcedRepository(
            event_store=event_store,
            aggregate_cls=V2Order,
            snapshot_store=snapshot_store,
            snapshot_schema_policy=schema_policy,
        )

        # Manually save a v1 snapshot (simulating an old snapshot)
        stale_snap = Snapshot(
            aggregate_id="order-1",
            version=2,
            state={"items": [{"name": "Widget", "price": 9.99}]},
            schema_version=1,  # Old schema!
        )
        await snapshot_store.save("V2Order", stale_snap)

        # The event stream has 3 events (including the 2 the snapshot covered)
        order = V2Order(id="order-1")
        order._apply(ItemAdded(order_id="order-1", item_name="A", price=1.0))
        order._apply(ItemAdded(order_id="order-1", item_name="B", price=2.0))
        order._apply(ItemAdded(order_id="order-1", item_name="C", price=3.0))
        await event_store.append_to_stream("order-1", order.pull_events(), 0)

        # Load — should skip stale snapshot, replay from scratch
        loaded = await repo.get_by_id("order-1")
        assert loaded is not None
        assert len(loaded.items) == 3

    @pytest.mark.anyio
    async def test_valid_snapshot_used_when_schema_matches(self) -> None:
        """A snapshot with matching schema version is used normally."""
        event_store = FakeEventStore()
        snapshot_store = FakeSnapshotStore()
        schema_policy = RejectStaleSnapshotPolicy()

        repo: EventSourcedRepository[V2Order, str] = EventSourcedRepository(
            event_store=event_store,
            aggregate_cls=V2Order,
            snapshot_store=snapshot_store,
            snapshot_schema_policy=schema_policy,
        )

        # Build up event stream: 3 events total
        order = V2Order(id="order-2")
        order._apply(ItemAdded(order_id="order-2", item_name="Widget", price=9.99))
        order._apply(ItemAdded(order_id="order-2", item_name="Gadget", price=5.0))
        order._apply(ItemAdded(order_id="order-2", item_name="Doohickey", price=3.0))
        await event_store.append_to_stream("order-2", order.pull_events(), 0)

        # Save a v2 snapshot at version 2 (matching schema, 2 items)
        valid_snap = Snapshot(
            aggregate_id="order-2",
            version=2,
            state={
                "items": [
                    {"name": "Widget", "price": 9.99},
                    {"name": "Gadget", "price": 5.0},
                ],
                "discount": 0.0,
            },
            schema_version=2,
        )
        await snapshot_store.save("V2Order", valid_snap)

        loaded = await repo.get_by_id("order-2")
        assert loaded is not None
        # Snapshot (2 items) + 1 event replayed from version 2 = 3 total
        assert loaded.version == 3
        assert len(loaded.items) == 3

    @pytest.mark.anyio
    async def test_no_schema_policy_uses_snapshot_regardless(self) -> None:
        """Without a schema policy, the repository uses snapshots without
        checking schema version (backward-compatible behavior)."""
        event_store = FakeEventStore()
        snapshot_store = FakeSnapshotStore()
        # No schema_policy configured

        repo: EventSourcedRepository[V2Order, str] = EventSourcedRepository(
            event_store=event_store,
            aggregate_cls=V2Order,
            snapshot_store=snapshot_store,
            # snapshot_schema_policy intentionally omitted
        )

        # Build event stream: 2 events
        order = V2Order(id="order-3")
        order._apply(ItemAdded(order_id="order-3", item_name="Widget", price=9.99))
        order._apply(ItemAdded(order_id="order-3", item_name="Gadget", price=5.0))
        await event_store.append_to_stream("order-3", order.pull_events(), 0)

        # Save a v1 snapshot at version 1 (would be stale, but no policy to check)
        stale_snap = Snapshot(
            aggregate_id="order-3",
            version=1,
            state={"items": [{"name": "Widget", "price": 9.99}]},
            schema_version=1,
        )
        await snapshot_store.save("V2Order", stale_snap)

        loaded = await repo.get_by_id("order-3")
        assert loaded is not None
        # Uses snapshot (v1 state) + 1 event replayed from version 1
        assert loaded.version == 2

    @pytest.mark.anyio
    async def test_stale_snapshot_with_no_events_returns_none(self) -> None:
        """If the snapshot is stale and there are no events at all
        (snapshot was orphaned), return None."""
        event_store = FakeEventStore()
        snapshot_store = FakeSnapshotStore()
        schema_policy = RejectStaleSnapshotPolicy()

        repo: EventSourcedRepository[V2Order, str] = EventSourcedRepository(
            event_store=event_store,
            aggregate_cls=V2Order,
            snapshot_store=snapshot_store,
            snapshot_schema_policy=schema_policy,
        )

        stale_snap = Snapshot(
            aggregate_id="order-ghost",
            version=5,
            state={},
            schema_version=1,
        )
        await snapshot_store.save("V2Order", stale_snap)

        loaded = await repo.get_by_id("order-ghost")
        assert loaded is None


# ===================================================================
# StaleSnapshotError
# ===================================================================


class TestStaleSnapshotError:
    """``StaleSnapshotError`` carries diagnostic information about the
    schema version mismatch."""

    def test_carries_aggregate_id(self) -> None:
        err = StaleSnapshotError("order-1", snapshot_version=1, expected_version=2)
        assert err.aggregate_id == "order-1"

    def test_carries_version_info(self) -> None:
        err = StaleSnapshotError("order-1", snapshot_version=1, expected_version=3)
        assert err.snapshot_schema_version == 1
        assert err.expected_schema_version == 3

    def test_message_includes_versions(self) -> None:
        err = StaleSnapshotError("order-1", snapshot_version=1, expected_version=2)
        msg = str(err)
        assert "order-1" in msg
        assert "1" in msg
        assert "2" in msg
