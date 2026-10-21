"""Run the ``SnapshotStore`` contract suite against ``FakeSnapshotStore``."""

from __future__ import annotations

import pytest

from pydomain.testing import FakeSnapshotStore
from pydomain.testing.contracts import SnapshotStoreContract


class TestFakeSnapshotStoreContract(SnapshotStoreContract):
    @pytest.fixture
    def snapshot_store(self) -> FakeSnapshotStore:
        return FakeSnapshotStore()
