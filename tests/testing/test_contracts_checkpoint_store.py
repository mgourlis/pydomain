"""Run the ``CheckpointStore`` contract suite against ``FakeCheckpointStore``."""

from __future__ import annotations

import pytest

from pydomain.testing import FakeCheckpointStore
from pydomain.testing.contracts import CheckpointStoreContract

pytestmark = pytest.mark.xfail(
    strict=True,
    raises=NotImplementedError,
    reason="DCE-110 contract cases not implemented yet",
)


class TestFakeCheckpointStoreContract(CheckpointStoreContract):
    @pytest.fixture
    def checkpoint_store(self) -> FakeCheckpointStore:
        return FakeCheckpointStore()
