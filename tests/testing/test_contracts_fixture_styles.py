"""A suite's adapter fixture may be async and may clean up after itself."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from pydomain.testing import FakeCheckpointStore
from pydomain.testing.contracts import CheckpointStoreContract


class TestAsyncGeneratorFixtureContract(CheckpointStoreContract):
    """Every case, including the Protocol check, runs with an async fixture."""

    @pytest.fixture
    async def checkpoint_store(self) -> AsyncIterator[FakeCheckpointStore]:
        yield FakeCheckpointStore()
