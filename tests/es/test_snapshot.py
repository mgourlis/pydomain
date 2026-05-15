# mypy: disable-error-code="attr-defined"
"""Tests for the Snapshot model and SnapshotStore protocol.

Covers the Pydantic model validation, the runtime-checkable Protocol
conformance, and the FakeSnapshotStore in-memory implementation semantics
(save/get round-trip, missing-key behaviour, overwrite idempotency, and
aggregate isolation).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pydomain.es.snapshot import Snapshot, SnapshotStore
from pydomain.testing.fake_snapshot_store import FakeSnapshotStore

# ===================================================================
# Snapshot Model Validation
# ===================================================================


class TestSnapshotModel:
    """The ``Snapshot`` Pydantic model validates its fields and provides
    sensible defaults."""

    def test_created_with_explicit_fields(self) -> None:
        """A Snapshot can be constructed with ``aggregate_id``, ``version``,
        ``state``, and ``created_at``."""
        dt = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)
        snap = Snapshot(
            aggregate_id="cart-001",
            version=3,
            state={"items": ["sku-001"], "total": 29.99},
            created_at=dt,
        )
        assert snap.aggregate_id == "cart-001"
        assert snap.version == 3
        assert snap.state == {"items": ["sku-001"], "total": 29.99}
        assert snap.created_at == dt

    def test_created_at_defaults_to_utc_now(self) -> None:
        """Omitting ``created_at`` defaults to the current UTC time."""
        snap = Snapshot(
            aggregate_id="cart-002",
            version=1,
            state={},
        )
        assert snap.created_at is not None
        assert snap.created_at.tzinfo == UTC
        # Should be very recent (within the last 5 seconds)
        now = datetime.now(UTC)
        assert (now - snap.created_at).total_seconds() < 5

    def test_is_pydantic_model_dump_and_validate(self) -> None:
        """Snapshot supports ``model_dump`` and ``model_validate`` from
        Pydantic v2."""
        snap = Snapshot(
            aggregate_id="cart-003",
            version=5,
            state={"note": "test"},
        )
        dumped = snap.model_dump()
        assert dumped["aggregate_id"] == "cart-003"
        assert dumped["version"] == 5
        assert dumped["state"] == {"note": "test"}
        assert "created_at" in dumped

        restored = Snapshot.model_validate(dumped)
        assert restored == snap

    def test_model_dump_round_trip_via_json(self) -> None:
        """Snapshot serialises to JSON and back correctly."""
        snap = Snapshot(
            aggregate_id="cart-004",
            version=2,
            state={"count": 10},
        )
        json_str = snap.model_dump_json()
        restored = Snapshot.model_validate_json(json_str)
        assert restored.aggregate_id == "cart-004"
        assert restored.version == 2
        assert restored.state == {"count": 10}


# ===================================================================
# SnapshotStore Protocol Conformance
# ===================================================================


class TestSnapshotStoreProtocol:
    """The ``SnapshotStore`` is ``@runtime_checkable`` -- any implementation
    must pass an ``isinstance`` check."""

    def test_fake_snapshot_store_passes_isinstance(self) -> None:
        """``isinstance(FakeSnapshotStore(), SnapshotStore)`` returns
        ``True``."""
        store = FakeSnapshotStore()
        assert isinstance(store, SnapshotStore)


# ===================================================================
# FakeSnapshotStore -- Save and Get
# ===================================================================


class TestFakeSnapshotStoreSaveGet:
    """``save()`` and ``get()`` -- round-trip semantics for the
    in-memory fake."""

    @pytest.mark.anyio
    async def test_save_and_get_round_trip(self) -> None:
        """Save a snapshot, then get it back with matching fields."""
        store = FakeSnapshotStore()
        snap = Snapshot(
            aggregate_id="cart-001",
            version=3,
            state={"items": ["sku-001"], "total": 29.99},
        )
        await store.save("cart", snap)

        retrieved = await store.get("cart", "cart-001")
        assert retrieved is not None
        assert retrieved.aggregate_id == "cart-001"
        assert retrieved.version == 3
        assert retrieved.state == {"items": ["sku-001"], "total": 29.99}
        assert retrieved.created_at == snap.created_at

    @pytest.mark.anyio
    async def test_get_returns_none_for_non_existent_key(self) -> None:
        """Getting a snapshot for a (aggregate_type, aggregate_id) pair
        that was never saved returns ``None``."""
        store = FakeSnapshotStore()
        result = await store.get("cart", "non-existent")
        assert result is None

    @pytest.mark.anyio
    async def test_get_returns_none_for_non_existent_aggregate_type(
        self,
    ) -> None:
        """Getting a snapshot with a different ``aggregate_type`` for
        an existing ``aggregate_id`` returns ``None``."""
        store = FakeSnapshotStore()
        snap = Snapshot(
            aggregate_id="order-001",
            version=1,
            state={},
        )
        await store.save("order", snap)

        # Same aggregate_id but different aggregate_type
        result = await store.get("cart", "order-001")
        assert result is None

    @pytest.mark.anyio
    async def test_get_returns_none_after_save_with_different_type(
        self,
    ) -> None:
        """Having saved a snapshot for one type does not make it
        retrievable under a different type (type isolation)."""
        store = FakeSnapshotStore()
        snap = Snapshot(
            aggregate_id="cart-001",
            version=1,
            state={},
        )
        await store.save("cart", snap)

        assert await store.get("cart", "cart-001") is not None
        assert await store.get("order", "cart-001") is None

    @pytest.mark.anyio
    async def test_multiple_aggregates_independent(
        self,
    ) -> None:
        """Snapshots for different (aggregate_type, aggregate_id)
        combinations are isolated."""
        store = FakeSnapshotStore()

        snap_a = Snapshot(
            aggregate_id="cart-001",
            version=2,
            state={"items": ["sku-a"]},
        )
        snap_b = Snapshot(
            aggregate_id="order-001",
            version=1,
            state={"total": 100.0},
        )
        await store.save("cart", snap_a)
        await store.save("order", snap_b)

        retrieved_a = await store.get("cart", "cart-001")
        assert retrieved_a is not None
        assert retrieved_a.state == {"items": ["sku-a"]}

        retrieved_b = await store.get("order", "order-001")
        assert retrieved_b is not None
        assert retrieved_b.state == {"total": 100.0}

    @pytest.mark.anyio
    async def test_multiple_snapshots_same_type_different_ids(
        self,
    ) -> None:
        """Multiple snapshots within the same ``aggregate_type`` but
        different ``aggregate_id`` values are independent."""
        store = FakeSnapshotStore()

        snap_1 = Snapshot(
            aggregate_id="cart-001",
            version=1,
            state={"items": []},
        )
        snap_2 = Snapshot(
            aggregate_id="cart-002",
            version=5,
            state={"items": ["sku-x"]},
        )
        await store.save("cart", snap_1)
        await store.save("cart", snap_2)

        r1 = await store.get("cart", "cart-001")
        r2 = await store.get("cart", "cart-002")
        assert r1 is not None
        assert r2 is not None
        assert r1.version == 1
        assert r2.version == 5

    @pytest.mark.anyio
    async def test_save_overwrites_existing_same_key(self) -> None:
        """Saving a new snapshot for the same (aggregate_type,
        aggregate_id) replaces the previous one without error."""
        store = FakeSnapshotStore()

        snap_old = Snapshot(
            aggregate_id="cart-001",
            version=2,
            state={"items": ["old"]},
        )
        snap_new = Snapshot(
            aggregate_id="cart-001",
            version=3,
            state={"items": ["new"]},
        )

        await store.save("cart", snap_old)
        await store.save("cart", snap_new)  # Overwrite -- no error

        retrieved = await store.get("cart", "cart-001")
        assert retrieved is not None
        assert retrieved.version == 3
        assert retrieved.state == {"items": ["new"]}

    @pytest.mark.anyio
    async def test_save_overwrite_does_not_add_duplicate_key(self) -> None:
        """Overwriting a snapshot does not create duplicate entries in
        the underlying store."""
        store = FakeSnapshotStore()

        snap_old = Snapshot(
            aggregate_id="cart-001",
            version=1,
            state={"items": []},
        )
        snap_new = Snapshot(
            aggregate_id="cart-001",
            version=2,
            state={"items": ["sku-001"]},
        )

        await store.save("cart", snap_old)
        await store.save("cart", snap_new)

        assert len(store._snapshots) == 1

    @pytest.mark.anyio
    async def test_save_with_empty_state_dict(self) -> None:
        """An empty ``state`` dict is a valid value and round-trips
        correctly."""
        store = FakeSnapshotStore()
        snap = Snapshot(
            aggregate_id="cart-001",
            version=1,
            state={},
        )
        await store.save("cart", snap)

        retrieved = await store.get("cart", "cart-001")
        assert retrieved is not None
        assert retrieved.state == {}

    @pytest.mark.anyio
    async def test_same_id_different_types_independent(self) -> None:
        """The same ``aggregate_id`` under different ``aggregate_type``
        values are stored independently."""
        store = FakeSnapshotStore()

        await store.save(
            "cart",
            Snapshot(aggregate_id="shared-001", version=1, state={"role": "cart"}),
        )
        await store.save(
            "order",
            Snapshot(aggregate_id="shared-001", version=10, state={"role": "order"}),
        )

        cart_snap = await store.get("cart", "shared-001")
        order_snap = await store.get("order", "shared-001")
        assert cart_snap is not None
        assert order_snap is not None
        assert cart_snap.state == {"role": "cart"}
        assert order_snap.state == {"role": "order"}
