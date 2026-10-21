"""Run the ``SagaRepository`` contract suite against ``FakeSagaRepository``."""

from __future__ import annotations

import pytest

from pydomain.testing import FakeSagaRepository
from pydomain.testing.contracts import SagaRepositoryContract

pytestmark = pytest.mark.xfail(
    strict=True,
    raises=NotImplementedError,
    reason="DCE-110 contract cases not implemented yet",
)


class TestFakeSagaRepositoryContract(SagaRepositoryContract):
    @pytest.fixture
    def saga_repository(self) -> FakeSagaRepository:
        return FakeSagaRepository()
