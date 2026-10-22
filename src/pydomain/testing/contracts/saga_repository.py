"""Contract suite for the ``SagaRepository`` Protocol (ADR-027, ADR-034, ADR-035)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from pydomain.cqrs.saga.repository import SagaRepository
from pydomain.cqrs.saga.state import SagaState, SagaStatus
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing.contracts._backend import AsyncioBackend

__all__ = ["SagaRepositoryContract"]

_SAGA_TYPE = "ContractOrderSaga"


class _ContractSagaNoted(DomainEvent):
    """Synthetic event a saga state carries into ``save``."""

    note: str


def _undispatched_command() -> dict[str, Any]:
    return {
        "command_type": "ReserveItems",
        "module_name": "contract.commands",
        "data": {"order_id": "order-1"},
        "dispatched": False,
    }


def _ids(states: Sequence[SagaState]) -> set[UUID]:
    return {state.id for state in states}


def _past() -> datetime:
    return datetime.now(UTC) - timedelta(days=1)


def _future() -> datetime:
    return datetime.now(UTC) + timedelta(days=1)


class SagaRepositoryContract(AsyncioBackend):
    """Cases every :class:`~pydomain.cqrs.saga.repository.SagaRepository` passes.

    Override the ``saga_repository`` fixture to return a fresh, empty
    repository for each test. Override :meth:`new_state` when your repository
    stores a ``SagaState`` subclass. States are compared by ``id`` and field
    values, so a repository that maps states to rows and back passes.

    Optimistic concurrency on ``save`` is not covered: the convention for a
    saga state's version (who increments it, and when) is unspecified
    (audit SAGA-07, cluster W2-5).
    """

    @pytest.fixture
    def saga_repository(self) -> SagaRepository:
        """Return a fresh, empty repository. Override in your subclass."""
        raise NotImplementedError(
            "Override the `saga_repository` fixture to return your adapter."
        )

    def new_state(self, **fields: Any) -> SagaState:
        """Return a new, never-saved saga state with *fields* set.

        The default builds a ``SagaState`` with a fresh ``correlation_id``.
        """
        fields.setdefault("saga_type", _SAGA_TYPE)
        fields.setdefault("correlation_id", uuid4())
        return SagaState(**fields)

    @pytest.mark.anyio
    async def test_satisfies_protocol(self, saga_repository: SagaRepository) -> None:
        """The repository is a ``SagaRepository``."""
        assert isinstance(saga_repository, SagaRepository)

    @pytest.mark.anyio
    async def test_get_by_id_returns_none_for_unknown_id(
        self, saga_repository: SagaRepository
    ) -> None:
        """Loading an id that was never saved returns ``None``."""
        assert await saga_repository.get_by_id(uuid4()) is None

    @pytest.mark.anyio
    async def test_saved_state_is_loaded_by_id(
        self, saga_repository: SagaRepository
    ) -> None:
        """``get_by_id`` returns the state ``save`` inserted."""
        state = self.new_state(status=SagaStatus.RUNNING, current_step="reserve")

        await saga_repository.save(state)
        loaded = await saga_repository.get_by_id(state.id)

        assert loaded is not None
        assert loaded.id == state.id
        assert loaded.saga_type == state.saga_type
        assert loaded.status == SagaStatus.RUNNING
        assert loaded.current_step == "reserve"
        assert loaded.correlation_id == state.correlation_id

    @pytest.mark.anyio
    async def test_saving_a_loaded_state_persists_its_changes(
        self, saga_repository: SagaRepository
    ) -> None:
        """Changes to a loaded state are visible after it is saved again."""
        state = self.new_state()
        await saga_repository.save(state)
        loaded = await saga_repository.get_by_id(state.id)
        assert loaded is not None

        loaded.status = SagaStatus.RUNNING
        loaded.current_step = "charge"
        await saga_repository.save(loaded)
        reloaded = await saga_repository.get_by_id(state.id)

        assert reloaded is not None
        assert reloaded.status == SagaStatus.RUNNING
        assert reloaded.current_step == "charge"

    @pytest.mark.anyio
    async def test_find_by_correlation_id_returns_matching_saga(
        self, saga_repository: SagaRepository
    ) -> None:
        """A saga is found by its correlation id and saga type."""
        state = self.new_state()
        await saga_repository.save(state)
        await saga_repository.save(self.new_state())
        assert state.correlation_id is not None

        found = await saga_repository.find_by_correlation_id(
            state.correlation_id, state.saga_type
        )

        assert found is not None
        assert found.id == state.id

    @pytest.mark.anyio
    async def test_find_by_correlation_id_requires_matching_saga_type(
        self, saga_repository: SagaRepository
    ) -> None:
        """A matching correlation id under another saga type is not a match."""
        state = self.new_state()
        await saga_repository.save(state)
        assert state.correlation_id is not None

        found = await saga_repository.find_by_correlation_id(
            state.correlation_id, "ContractOtherSaga"
        )

        assert found is None

    @pytest.mark.anyio
    async def test_find_by_correlation_id_returns_none_for_unknown_id(
        self, saga_repository: SagaRepository
    ) -> None:
        """An unknown correlation id finds nothing."""
        await saga_repository.save(self.new_state())

        assert await saga_repository.find_by_correlation_id(uuid4(), _SAGA_TYPE) is None

    @pytest.mark.anyio
    async def test_pending_command_entries_keep_their_extra_keys(
        self, saga_repository: SagaRepository
    ) -> None:
        """Every key of a ``pending_commands`` entry survives save and load.

        ``SagaManager`` adds ``"suspended_by_recovery"`` next to the four
        documented keys to mark the suspension recovery created; an adapter
        that kept only the documented keys would lose it (ADR-077, B-95).
        """
        entry = {**_undispatched_command(), "suspended_by_recovery": True}
        state = self.new_state(status=SagaStatus.SUSPENDED, pending_commands=[entry])

        await saga_repository.save(state)
        loaded = await saga_repository.get_by_id(state.id)

        assert loaded is not None
        assert loaded.pending_commands == [entry]

    @pytest.mark.anyio
    async def test_find_suspended_sagas_returns_only_suspended(
        self, saga_repository: SagaRepository
    ) -> None:
        """Only sagas in ``SUSPENDED`` status are returned."""
        suspended = self.new_state(status=SagaStatus.SUSPENDED)
        running = self.new_state(status=SagaStatus.RUNNING)
        await saga_repository.save(suspended)
        await saga_repository.save(running)

        found = await saga_repository.find_suspended_sagas()

        assert _ids(found) == {suspended.id}

    @pytest.mark.anyio
    async def test_find_suspended_sagas_respects_limit(
        self, saga_repository: SagaRepository
    ) -> None:
        """``limit`` caps the number of sagas returned."""
        for _ in range(3):
            await saga_repository.save(self.new_state(status=SagaStatus.SUSPENDED))

        assert len(await saga_repository.find_suspended_sagas(limit=2)) == 2

    @pytest.mark.anyio
    async def test_find_expired_suspended_sagas_returns_only_expired(
        self, saga_repository: SagaRepository
    ) -> None:
        """Only suspended sagas whose ``timeout_at`` has passed are returned."""
        expired = self.new_state(status=SagaStatus.SUSPENDED, timeout_at=_past())
        not_due = self.new_state(status=SagaStatus.SUSPENDED, timeout_at=_future())
        no_timeout = self.new_state(status=SagaStatus.SUSPENDED)
        running = self.new_state(status=SagaStatus.RUNNING, timeout_at=_past())
        for state in (expired, not_due, no_timeout, running):
            await saga_repository.save(state)

        found = await saga_repository.find_expired_suspended_sagas()

        assert _ids(found) == {expired.id}

    @pytest.mark.anyio
    async def test_find_expired_suspended_sagas_respects_limit(
        self, saga_repository: SagaRepository
    ) -> None:
        """``limit`` caps the number of sagas returned."""
        for _ in range(3):
            await saga_repository.save(
                self.new_state(status=SagaStatus.SUSPENDED, timeout_at=_past())
            )

        assert len(await saga_repository.find_expired_suspended_sagas(limit=2)) == 2

    @pytest.mark.anyio
    async def test_find_stalled_sagas_returns_saga_with_undispatched_command(
        self, saga_repository: SagaRepository
    ) -> None:
        """A saga with an undispatched command is stalled; one without is not."""
        stalled = self.new_state(
            status=SagaStatus.RUNNING, pending_commands=[_undispatched_command()]
        )
        idle = self.new_state(status=SagaStatus.RUNNING)
        await saga_repository.save(stalled)
        await saga_repository.save(idle)

        found = await saga_repository.find_stalled_sagas()

        assert _ids(found) == {stalled.id}

    @pytest.mark.anyio
    async def test_find_stalled_sagas_returns_compensating_saga(
        self, saga_repository: SagaRepository
    ) -> None:
        """A ``COMPENSATING`` saga is stalled even with no pending commands.

        ``SagaManager.recover_pending_sagas`` finishes a compensation that a
        crash interrupted only if the repository returns it here (ADR-077).
        """
        compensating = self.new_state(status=SagaStatus.COMPENSATING)
        idle = self.new_state(status=SagaStatus.RUNNING)
        await saga_repository.save(compensating)
        await saga_repository.save(idle)

        found = await saga_repository.find_stalled_sagas()

        assert _ids(found) == {compensating.id}

    @pytest.mark.anyio
    async def test_find_stalled_sagas_respects_limit(
        self, saga_repository: SagaRepository
    ) -> None:
        """``limit`` caps the number of sagas returned."""
        for _ in range(3):
            await saga_repository.save(
                self.new_state(
                    status=SagaStatus.RUNNING,
                    pending_commands=[_undispatched_command()],
                )
            )

        assert len(await saga_repository.find_stalled_sagas(limit=2)) == 2

    @pytest.mark.anyio
    async def test_save_drains_state_events_into_pull_events(
        self, saga_repository: SagaRepository
    ) -> None:
        """``save`` drains the state's events; ``pull_events`` hands them over once."""
        state = self.new_state()
        event = _ContractSagaNoted(note="reserved")
        state._add_event(event)  # pyright: ignore[reportPrivateUsage]

        await saga_repository.save(state)

        assert state.pull_events() == []
        assert [e.event_id for e in saga_repository.pull_events()] == [event.event_id]
        assert saga_repository.pull_events() == []

    @pytest.mark.anyio
    async def test_saving_a_loaded_state_does_not_re_emit_drained_events(
        self, saga_repository: SagaRepository
    ) -> None:
        """An event ``save`` drained is handed over once, not on later saves."""
        state = self.new_state()
        state._add_event(_ContractSagaNoted(note="reserved"))  # pyright: ignore[reportPrivateUsage]
        await saga_repository.save(state)
        saga_repository.pull_events()

        loaded = await saga_repository.get_by_id(state.id)
        assert loaded is not None
        await saga_repository.save(loaded)

        assert saga_repository.pull_events() == []
