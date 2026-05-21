"""Tests for FakeSagaRepository — in-memory saga repository for testing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from pydomain.cqrs.saga.state import SagaState, SagaStatus
from pydomain.testing.fake_saga_repository import FakeSagaRepository


class TestGetById:
    """get_by_id() — found and not-found paths."""

    @pytest.mark.anyio
    async def test_returns_state_when_found(self) -> None:
        repo = FakeSagaRepository()
        state = SagaState(saga_type="TestSaga")
        await repo.save(state)
        result = await repo.get_by_id(state.id)
        assert result is not None
        assert result.id == state.id

    @pytest.mark.anyio
    async def test_returns_none_when_not_found(self) -> None:
        repo = FakeSagaRepository()
        result = await repo.get_by_id(uuid4())
        assert result is None


class TestFindByCorrelationId:
    """find_by_correlation_id() — match, mismatch, and empty store."""

    @pytest.mark.anyio
    async def test_finds_matching_state(self) -> None:
        repo = FakeSagaRepository()
        cid = uuid4()
        state = SagaState(saga_type="OrderSaga", correlation_id=cid)
        await repo.save(state)

        result = await repo.find_by_correlation_id(cid, "OrderSaga")
        assert result is not None
        assert result.id == state.id

    @pytest.mark.anyio
    async def test_returns_none_when_cid_matches_but_type_does_not(
        self,
    ) -> None:
        repo = FakeSagaRepository()
        cid = uuid4()
        state = SagaState(saga_type="OrderSaga", correlation_id=cid)
        await repo.save(state)

        result = await repo.find_by_correlation_id(cid, "OtherSaga")
        assert result is None

    @pytest.mark.anyio
    async def test_returns_none_when_type_matches_but_cid_does_not(
        self,
    ) -> None:
        repo = FakeSagaRepository()
        state = SagaState(saga_type="OrderSaga", correlation_id=uuid4())
        await repo.save(state)

        result = await repo.find_by_correlation_id(uuid4(), "OrderSaga")
        assert result is None

    @pytest.mark.anyio
    async def test_returns_none_on_empty_store(self) -> None:
        repo = FakeSagaRepository()
        result = await repo.find_by_correlation_id(uuid4(), "OrderSaga")
        assert result is None


class TestFindStalledSagas:
    """find_stalled_sagas() — sagas with undispatched pending commands."""

    @pytest.mark.anyio
    async def test_finds_saga_with_undispatched_commands(self) -> None:
        repo = FakeSagaRepository()
        state = SagaState(
            saga_type="OrderSaga",
            pending_commands=[
                {"command_type": "Reserve", "data": {}, "dispatched": False},
            ],
        )
        await repo.save(state)

        stalled = await repo.find_stalled_sagas()
        assert len(stalled) == 1
        assert stalled[0].id == state.id

    @pytest.mark.anyio
    async def test_includes_fully_dispatched_sagas_for_cleanup(self) -> None:
        """Fully-dispatched sagas are still returned so the manager can clean up."""
        repo = FakeSagaRepository()
        state = SagaState(
            saga_type="OrderSaga",
            pending_commands=[
                {"command_type": "Reserve", "data": {}, "dispatched": True},
            ],
        )
        await repo.save(state)

        stalled = await repo.find_stalled_sagas()
        assert len(stalled) == 1
        assert stalled[0].id == state.id

    @pytest.mark.anyio
    async def test_ignores_terminal_sagas(self) -> None:
        repo = FakeSagaRepository()
        state = SagaState(
            saga_type="OrderSaga",
            status=SagaStatus.COMPLETED,
            pending_commands=[
                {"command_type": "Reserve", "data": {}, "dispatched": False},
            ],
        )
        await repo.save(state)

        stalled = await repo.find_stalled_sagas()
        assert stalled == []

    @pytest.mark.anyio
    async def test_ignores_sagas_with_no_pending_commands(self) -> None:
        repo = FakeSagaRepository()
        state = SagaState(saga_type="OrderSaga", pending_commands=[])
        await repo.save(state)

        stalled = await repo.find_stalled_sagas()
        assert stalled == []

    @pytest.mark.anyio
    async def test_respects_limit(self) -> None:
        repo = FakeSagaRepository()
        for i in range(5):
            state = SagaState(
                saga_type=f"Saga{i}",
                pending_commands=[
                    {"command_type": "Cmd", "data": {}, "dispatched": False},
                ],
            )
            await repo.save(state)

        stalled = await repo.find_stalled_sagas(limit=2)
        assert len(stalled) == 2


class TestFindSuspendedSagas:
    """find_suspended_sagas() — sagas in SUSPENDED status."""

    @pytest.mark.anyio
    async def test_finds_suspended_sagas(self) -> None:
        repo = FakeSagaRepository()
        state = SagaState(
            saga_type="OrderSaga",
            status=SagaStatus.SUSPENDED,
            suspension_reason="waiting",
        )
        await repo.save(state)

        suspended = await repo.find_suspended_sagas()
        assert len(suspended) == 1
        assert suspended[0].id == state.id

    @pytest.mark.anyio
    async def test_ignores_non_suspended_sagas(self) -> None:
        repo = FakeSagaRepository()
        state = SagaState(saga_type="OrderSaga", status=SagaStatus.RUNNING)
        await repo.save(state)

        suspended = await repo.find_suspended_sagas()
        assert suspended == []

    @pytest.mark.anyio
    async def test_respects_limit(self) -> None:
        repo = FakeSagaRepository()
        for i in range(5):
            state = SagaState(saga_type=f"Saga{i}", status=SagaStatus.SUSPENDED)
            await repo.save(state)

        suspended = await repo.find_suspended_sagas(limit=2)
        assert len(suspended) == 2


class TestPullEvents:
    """pull_events() — drain collected events from save()."""

    @pytest.mark.anyio
    async def test_pull_events_returns_empty_initially(self) -> None:
        repo = FakeSagaRepository()
        events = repo.pull_events()
        assert events == []

    @pytest.mark.anyio
    async def test_pull_events_drains_buffer(self) -> None:
        repo = FakeSagaRepository()
        state = SagaState(saga_type="OrderSaga")
        await repo.save(state)
        # save() calls pull_events() on the state, which returns []
        # but the repo still collects them

        # Now pull from the repo — should be empty since state had no events
        events = repo.pull_events()
        assert events == []

    @pytest.mark.anyio
    async def test_pull_events_clears_after_drain(self) -> None:
        repo = FakeSagaRepository()
        events = repo.pull_events()
        assert events == []
        # Second call should also be empty
        assert repo.pull_events() == []


class TestFindExpiredSuspendedSagas:
    """find_expired_suspended_sagas() — SUSPENDED sagas with past timeout_at."""

    @pytest.mark.anyio
    async def test_finds_expired_suspended_sagas(self) -> None:
        repo = FakeSagaRepository()
        state = SagaState(
            saga_type="OrderSaga",
            status=SagaStatus.SUSPENDED,
            timeout_at=datetime.now(UTC) - timedelta(hours=1),
        )
        await repo.save(state)

        expired = await repo.find_expired_suspended_sagas()
        assert len(expired) == 1
        assert expired[0].id == state.id

    @pytest.mark.anyio
    async def test_ignores_suspended_without_timeout(self) -> None:
        repo = FakeSagaRepository()
        state = SagaState(
            saga_type="OrderSaga",
            status=SagaStatus.SUSPENDED,
            timeout_at=None,
        )
        await repo.save(state)

        expired = await repo.find_expired_suspended_sagas()
        assert expired == []

    @pytest.mark.anyio
    async def test_ignores_suspended_with_future_timeout(self) -> None:
        repo = FakeSagaRepository()
        state = SagaState(
            saga_type="OrderSaga",
            status=SagaStatus.SUSPENDED,
            timeout_at=datetime.now(UTC) + timedelta(hours=1),
        )
        await repo.save(state)

        expired = await repo.find_expired_suspended_sagas()
        assert expired == []

    @pytest.mark.anyio
    async def test_ignores_non_suspended_sagas(self) -> None:
        repo = FakeSagaRepository()
        state = SagaState(
            saga_type="OrderSaga",
            status=SagaStatus.RUNNING,
            timeout_at=datetime.now(UTC) - timedelta(hours=1),
        )
        await repo.save(state)

        expired = await repo.find_expired_suspended_sagas()
        assert expired == []

    @pytest.mark.anyio
    async def test_respects_limit(self) -> None:
        repo = FakeSagaRepository()
        for i in range(5):
            state = SagaState(
                saga_type=f"Saga{i}",
                status=SagaStatus.SUSPENDED,
                timeout_at=datetime.now(UTC) - timedelta(hours=1),
            )
            await repo.save(state)

        expired = await repo.find_expired_suspended_sagas(limit=2)
        assert len(expired) == 2
