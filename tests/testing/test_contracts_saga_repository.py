"""Run the ``SagaRepository`` contract suite against ``FakeSagaRepository``."""

from __future__ import annotations

from typing import Any

import pytest

from pydomain.testing import FakeSagaRepository
from pydomain.testing.contracts import SagaRepositoryContract


class TestFakeSagaRepositoryContract(SagaRepositoryContract):
    @pytest.fixture
    def saga_repository(self) -> FakeSagaRepository:
        return FakeSagaRepository()

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-120 FAKE-12: the stored copy keeps the drained events",
    )
    @pytest.mark.anyio
    async def test_saving_a_loaded_state_does_not_re_emit_drained_events(
        self, saga_repository: Any
    ) -> None:
        await super().test_saving_a_loaded_state_does_not_re_emit_drained_events(
            saga_repository
        )
