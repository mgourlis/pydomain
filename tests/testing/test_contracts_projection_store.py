"""Run the ``ProjectionStore`` contract suite against ``InMemoryProjectionStore``."""

from __future__ import annotations

import pytest

from pydomain.testing import InMemoryProjectionStore
from pydomain.testing.contracts import ProjectionStoreContract


class TestInMemoryProjectionStoreContract(ProjectionStoreContract):
    @pytest.fixture
    def projection_store(self) -> InMemoryProjectionStore:
        return InMemoryProjectionStore()
