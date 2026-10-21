"""Run the ``SnapshotStore`` contract suite against ``FakeSnapshotStore``."""

from __future__ import annotations

import pytest

from pydomain.testing import FakeSnapshotStore
from pydomain.testing.contracts import SnapshotStoreContract

pytestmark = pytest.mark.xfail(
    strict=True,
    raises=NotImplementedError,
    reason="DCE-110 contract cases not implemented yet",
)


class TestFakeSnapshotStoreContract(SnapshotStoreContract):
    @pytest.fixture
    def snapshot_store(self) -> FakeSnapshotStore:
        return FakeSnapshotStore()
