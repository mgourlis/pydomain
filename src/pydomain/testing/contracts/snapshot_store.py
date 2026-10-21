"""Contract suite for the ``SnapshotStore`` Protocol (ADR-043, ADR-053)."""

from __future__ import annotations

import pytest

from pydomain.es.snapshot import Snapshot, SnapshotStore
from pydomain.testing.contracts._backend import AsyncioBackend

__all__ = ["SnapshotStoreContract"]

_ORDER = "ContractOrder"
_INVOICE = "ContractInvoice"


def _snapshot(aggregate_id: str, version: int) -> Snapshot:
    return Snapshot(
        aggregate_id=aggregate_id,
        version=version,
        state={
            "id": aggregate_id,
            "status": "placed",
            "line_count": version,
            "lines": [{"sku": f"sku-{n}", "quantity": 1} for n in range(version)],
        },
        schema_version=2,
    )


def _assert_same(actual: Snapshot | None, expected: Snapshot) -> None:
    assert actual is not None
    assert actual.aggregate_id == expected.aggregate_id
    assert actual.version == expected.version
    assert actual.state == expected.state
    assert actual.schema_version == expected.schema_version


class SnapshotStoreContract(AsyncioBackend):
    """Cases every :class:`~pydomain.es.snapshot.SnapshotStore` passes.

    Override the ``snapshot_store`` fixture to return a fresh, empty store
    for each test.
    """

    @pytest.fixture
    def snapshot_store(self) -> SnapshotStore:
        """Return a fresh, empty store. Override in your subclass."""
        raise NotImplementedError(
            "Override the `snapshot_store` fixture to return your adapter."
        )

    @pytest.mark.anyio
    async def test_satisfies_protocol(self, snapshot_store: SnapshotStore) -> None:
        """The store is a ``SnapshotStore``."""
        assert isinstance(snapshot_store, SnapshotStore)

    @pytest.mark.anyio
    async def test_get_returns_none_without_snapshot(
        self, snapshot_store: SnapshotStore
    ) -> None:
        """``get`` returns ``None`` when no snapshot exists."""
        assert await snapshot_store.get(_ORDER, "order-1") is None

    @pytest.mark.anyio
    async def test_get_returns_saved_snapshot(
        self, snapshot_store: SnapshotStore
    ) -> None:
        """``get`` returns the snapshot ``save`` persisted, field for field."""
        snapshot = _snapshot("order-1", 5)

        await snapshot_store.save(_ORDER, snapshot)

        _assert_same(await snapshot_store.get(_ORDER, "order-1"), snapshot)

    @pytest.mark.anyio
    async def test_get_returns_latest_snapshot(
        self, snapshot_store: SnapshotStore
    ) -> None:
        """After two saves, ``get`` returns the later, higher-version snapshot."""
        await snapshot_store.save(_ORDER, _snapshot("order-1", 5))
        latest = _snapshot("order-1", 10)
        await snapshot_store.save(_ORDER, latest)

        _assert_same(await snapshot_store.get(_ORDER, "order-1"), latest)

    @pytest.mark.anyio
    async def test_snapshots_are_kept_per_aggregate_type(
        self, snapshot_store: SnapshotStore
    ) -> None:
        """The same aggregate id under two aggregate types is two snapshots."""
        order = _snapshot("shared-1", 3)
        invoice = _snapshot("shared-1", 7)

        await snapshot_store.save(_ORDER, order)
        await snapshot_store.save(_INVOICE, invoice)

        _assert_same(await snapshot_store.get(_ORDER, "shared-1"), order)
        _assert_same(await snapshot_store.get(_INVOICE, "shared-1"), invoice)

    @pytest.mark.anyio
    async def test_snapshots_are_kept_per_aggregate_id(
        self, snapshot_store: SnapshotStore
    ) -> None:
        """Snapshots of two aggregates of one type do not overwrite each other."""
        first = _snapshot("order-1", 3)
        second = _snapshot("order-2", 4)

        await snapshot_store.save(_ORDER, first)
        await snapshot_store.save(_ORDER, second)

        _assert_same(await snapshot_store.get(_ORDER, "order-1"), first)
        _assert_same(await snapshot_store.get(_ORDER, "order-2"), second)

    @pytest.mark.anyio
    async def test_changing_a_saved_snapshot_does_not_change_the_stored_one(
        self, snapshot_store: SnapshotStore
    ) -> None:
        """The store keeps what ``save`` was given: changing that object
        afterwards, nested state included, does not change what ``get``
        returns (ADR-075)."""
        snapshot = _snapshot("order-1", 2)
        expected = snapshot.model_copy(deep=True)
        await snapshot_store.save(_ORDER, snapshot)

        snapshot.state["status"] = "cancelled"
        snapshot.state["lines"].append({"sku": "sku-extra", "quantity": 9})

        _assert_same(await snapshot_store.get(_ORDER, "order-1"), expected)

    @pytest.mark.anyio
    async def test_changing_a_returned_snapshot_does_not_change_the_stored_one(
        self, snapshot_store: SnapshotStore
    ) -> None:
        """Each ``get`` returns a snapshot of its own: changing one, nested
        state included, does not change what the next ``get`` returns
        (ADR-075)."""
        snapshot = _snapshot("order-1", 2)
        expected = snapshot.model_copy(deep=True)
        await snapshot_store.save(_ORDER, snapshot)

        returned = await snapshot_store.get(_ORDER, "order-1")
        assert returned is not None
        returned.state["status"] = "cancelled"
        returned.state["lines"].append({"sku": "sku-extra", "quantity": 9})

        _assert_same(await snapshot_store.get(_ORDER, "order-1"), expected)
