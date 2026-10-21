"""Contract suite for the ``SnapshotStore`` Protocol (ADR-043, ADR-053)."""

from __future__ import annotations

import pytest

from pydomain.es.snapshot import Snapshot, SnapshotStore
from pydomain.testing.contracts._support import pending

__all__ = ["SnapshotStoreContract"]

_ORDER = "ContractOrder"
_INVOICE = "ContractInvoice"


def _snapshot(aggregate_id: str, version: int) -> Snapshot:
    return Snapshot(
        aggregate_id=aggregate_id,
        version=version,
        state={"id": aggregate_id, "status": "placed", "line_count": version},
        schema_version=2,
    )


def _assert_same(actual: Snapshot | None, expected: Snapshot) -> None:
    assert actual is not None
    assert actual.aggregate_id == expected.aggregate_id
    assert actual.version == expected.version
    assert actual.state == expected.state
    assert actual.schema_version == expected.schema_version


class SnapshotStoreContract:
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

    def test_satisfies_protocol(self, snapshot_store: SnapshotStore) -> None:
        """The store is a ``SnapshotStore``."""
        pending()

    @pytest.mark.anyio
    async def test_get_returns_none_without_snapshot(
        self, snapshot_store: SnapshotStore
    ) -> None:
        """``get`` returns ``None`` when no snapshot exists."""
        pending()

    @pytest.mark.anyio
    async def test_get_returns_saved_snapshot(
        self, snapshot_store: SnapshotStore
    ) -> None:
        """``get`` returns the snapshot ``save`` persisted, field for field."""
        pending()

    @pytest.mark.anyio
    async def test_get_returns_latest_snapshot(
        self, snapshot_store: SnapshotStore
    ) -> None:
        """After two saves, ``get`` returns the later, higher-version snapshot."""
        pending()

    @pytest.mark.anyio
    async def test_snapshots_are_kept_per_aggregate_type(
        self, snapshot_store: SnapshotStore
    ) -> None:
        """The same aggregate id under two aggregate types is two snapshots."""
        pending()

    @pytest.mark.anyio
    async def test_snapshots_are_kept_per_aggregate_id(
        self, snapshot_store: SnapshotStore
    ) -> None:
        """Snapshots of two aggregates of one type do not overwrite each other."""
        pending()
