"""Contract suite for the ``SagaRepository`` Protocol (ADR-027, ADR-034, ADR-035)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from pydomain.cqrs.saga.repository import SagaRepository
from pydomain.cqrs.saga.state import SagaState
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing.contracts._support import pending

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


class SagaRepositoryContract:
    """Cases every :class:`~pydomain.cqrs.saga.repository.SagaRepository` passes.

    Override the ``saga_repository`` fixture to return a fresh, empty
    repository for each test. Override :meth:`new_state` when your repository
    stores a ``SagaState`` subclass. States are compared by ``id`` and field
    values, so a repository that maps states to rows and back passes.
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

    def test_satisfies_protocol(self, saga_repository: SagaRepository) -> None:
        """The repository is a ``SagaRepository``."""
        pending()

    @pytest.mark.anyio
    async def test_get_by_id_returns_none_for_unknown_id(
        self, saga_repository: SagaRepository
    ) -> None:
        """Loading an id that was never saved returns ``None``."""
        pending()

    @pytest.mark.anyio
    async def test_saved_state_is_loaded_by_id(
        self, saga_repository: SagaRepository
    ) -> None:
        """``get_by_id`` returns the state ``save`` inserted."""
        pending()

    @pytest.mark.anyio
    async def test_saving_a_loaded_state_persists_its_changes(
        self, saga_repository: SagaRepository
    ) -> None:
        """Changes to a loaded state are visible after it is saved again."""
        pending()

    @pytest.mark.anyio
    async def test_find_by_correlation_id_returns_matching_saga(
        self, saga_repository: SagaRepository
    ) -> None:
        """A saga is found by its correlation id and saga type."""
        pending()

    @pytest.mark.anyio
    async def test_find_by_correlation_id_requires_matching_saga_type(
        self, saga_repository: SagaRepository
    ) -> None:
        """A matching correlation id under another saga type is not a match."""
        pending()

    @pytest.mark.anyio
    async def test_find_by_correlation_id_returns_none_for_unknown_id(
        self, saga_repository: SagaRepository
    ) -> None:
        """An unknown correlation id finds nothing."""
        pending()

    @pytest.mark.anyio
    async def test_find_suspended_sagas_returns_only_suspended(
        self, saga_repository: SagaRepository
    ) -> None:
        """Only sagas in ``SUSPENDED`` status are returned."""
        pending()

    @pytest.mark.anyio
    async def test_find_suspended_sagas_respects_limit(
        self, saga_repository: SagaRepository
    ) -> None:
        """``limit`` caps the number of sagas returned."""
        pending()

    @pytest.mark.anyio
    async def test_find_expired_suspended_sagas_returns_only_expired(
        self, saga_repository: SagaRepository
    ) -> None:
        """Only suspended sagas whose ``timeout_at`` has passed are returned."""
        pending()

    @pytest.mark.anyio
    async def test_find_expired_suspended_sagas_respects_limit(
        self, saga_repository: SagaRepository
    ) -> None:
        """``limit`` caps the number of sagas returned."""
        pending()

    @pytest.mark.anyio
    async def test_find_stalled_sagas_returns_saga_with_undispatched_command(
        self, saga_repository: SagaRepository
    ) -> None:
        """A saga with an undispatched command is stalled; one without is not."""
        pending()

    @pytest.mark.anyio
    async def test_find_stalled_sagas_respects_limit(
        self, saga_repository: SagaRepository
    ) -> None:
        """``limit`` caps the number of sagas returned."""
        pending()

    @pytest.mark.anyio
    async def test_save_drains_state_events_into_pull_events(
        self, saga_repository: SagaRepository
    ) -> None:
        """``save`` drains the state's events; ``pull_events`` hands them over once."""
        pending()
