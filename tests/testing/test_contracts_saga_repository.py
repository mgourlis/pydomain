"""Run the ``SagaRepository`` contract suite against ``FakeSagaRepository``."""

from __future__ import annotations

import pytest

from pydomain.cqrs.saga.state import SagaState
from pydomain.testing import FakeSagaRepository
from pydomain.testing.contracts import SagaRepositoryContract


class TestFakeSagaRepositoryContract(SagaRepositoryContract):
    @pytest.fixture
    def saga_repository(self) -> FakeSagaRepository:
        return FakeSagaRepository()


class _DocumentedKeysOnlyRepository(FakeSagaRepository):
    """A broken adapter: keeps only the four documented pending-entry keys."""

    async def save(self, state: SagaState) -> None:
        await super().save(state)
        stored = self._store[state.id]
        stored.pending_commands = [
            {key: entry[key] for key in _DOCUMENTED_KEYS if key in entry}
            for entry in stored.pending_commands
        ]


_DOCUMENTED_KEYS = ("command_type", "module_name", "data", "dispatched")


@pytest.mark.anyio
async def test_extra_keys_case_fails_an_adapter_that_drops_them() -> None:
    """``test_pending_command_entries_keep_their_extra_keys`` catches an
    adapter that stores only the documented keys (ADR-077, B-95)."""
    case = SagaRepositoryContract()
    with pytest.raises(AssertionError):
        await case.test_pending_command_entries_keep_their_extra_keys(
            _DocumentedKeysOnlyRepository()
        )
