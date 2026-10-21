"""Run the ``SagaRepository`` contract suite against ``FakeSagaRepository``."""

from __future__ import annotations

import pytest

from pydomain.testing import FakeSagaRepository
from pydomain.testing.contracts import SagaRepositoryContract


class TestFakeSagaRepositoryContract(SagaRepositoryContract):
    @pytest.fixture
    def saga_repository(self) -> FakeSagaRepository:
        return FakeSagaRepository()
