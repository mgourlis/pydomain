"""Run the ``SnapshotStore`` contract suite against ``FakeSnapshotStore``."""

from __future__ import annotations

from typing import Any

import pytest

from pydomain.testing import FakeSnapshotStore
from pydomain.testing.contracts import SnapshotStoreContract


class TestFakeSnapshotStoreContract(SnapshotStoreContract):
    @pytest.fixture
    def snapshot_store(self) -> FakeSnapshotStore:
        return FakeSnapshotStore()

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-119 ES-09: the fake stores the caller's snapshot object",
    )
    @pytest.mark.anyio
    async def test_changing_a_saved_snapshot_does_not_change_the_stored_one(
        self, snapshot_store: Any
    ) -> None:
        await super().test_changing_a_saved_snapshot_does_not_change_the_stored_one(
            snapshot_store
        )

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-119 ES-09: the fake returns the snapshot object it holds",
    )
    @pytest.mark.anyio
    async def test_changing_a_returned_snapshot_does_not_change_the_stored_one(
        self, snapshot_store: Any
    ) -> None:
        await super().test_changing_a_returned_snapshot_does_not_change_the_stored_one(
            snapshot_store
        )
