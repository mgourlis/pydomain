"""Contract suite for the ``Repository`` Protocol (ADR-001, ADR-050)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.repository import Repository
from pydomain.testing.contracts._support import pending

__all__ = ["RepositoryContract"]


def _pending_event_ids(aggregate: AggregateRoot[Any]) -> list[UUID]:
    """Read the ids of the events *aggregate* has not yet handed over.

    Reads the buffer without draining it, so the repository under test still
    receives the events on ``save``.
    """
    pending: list[DomainEvent] = aggregate._pending_events  # pyright: ignore[reportPrivateUsage]
    return [event.event_id for event in pending]


class RepositoryContract:
    """Cases every :class:`~pydomain.ddd.repository.Repository` passes.

    Override the ``repository`` fixture to return a fresh, empty repository for
    each test, and :meth:`new_aggregate` to build the aggregates it stores.
    Aggregates are compared by ``id`` and events by ``event_id``, so a
    repository that maps aggregates to rows and back passes.
    """

    @pytest.fixture
    def repository(self) -> Repository[Any, Any]:
        """Return a fresh, empty repository. Override in your subclass."""
        raise NotImplementedError(
            "Override the `repository` fixture to return your adapter."
        )

    def new_aggregate(self) -> AggregateRoot[Any]:
        """Return a never-saved aggregate with a unique id.

        It must carry at least one pending domain event, as a factory method
        such as ``Order.place(...)`` leaves it. Override in your subclass.
        """
        raise NotImplementedError(
            "Override `new_aggregate` to return a new aggregate with a pending event."
        )

    def test_satisfies_protocol(self, repository: Repository[Any, Any]) -> None:
        """The repository is a ``Repository``."""
        pending()

    @pytest.mark.anyio
    async def test_get_by_id_returns_none_for_unknown_id(
        self, repository: Repository[Any, Any]
    ) -> None:
        """Loading an id that was never saved returns ``None``."""
        pending()

    @pytest.mark.anyio
    async def test_saved_aggregate_is_loaded_by_id(
        self, repository: Repository[Any, Any]
    ) -> None:
        """``get_by_id`` returns the aggregate ``save`` inserted."""
        pending()

    @pytest.mark.anyio
    async def test_save_drains_pending_events_from_aggregate(
        self, repository: Repository[Any, Any]
    ) -> None:
        """After ``save`` the aggregate has no pending events left."""
        pending()

    @pytest.mark.anyio
    async def test_pull_events_returns_events_drained_by_save(
        self, repository: Repository[Any, Any]
    ) -> None:
        """``pull_events`` hands over the events ``save`` drained, in order."""
        pending()

    @pytest.mark.anyio
    async def test_pull_events_clears_buffer(
        self, repository: Repository[Any, Any]
    ) -> None:
        """A second ``pull_events`` with no save in between returns nothing."""
        pending()

    @pytest.mark.anyio
    async def test_pull_events_collects_events_of_every_save(
        self, repository: Repository[Any, Any]
    ) -> None:
        """Events of several saves accumulate until ``pull_events``."""
        pending()

    @pytest.mark.anyio
    async def test_loaded_aggregate_can_be_saved_again(
        self, repository: Repository[Any, Any]
    ) -> None:
        """Saving the current version of a stored aggregate is an update."""
        pending()

    @pytest.mark.anyio
    async def test_save_with_mismatched_version_raises_concurrency_error(
        self, repository: Repository[Any, Any]
    ) -> None:
        """Updating with a version the store does not hold is a conflict."""
        pending()

    @pytest.mark.anyio
    async def test_save_accepts_command_id(
        self, repository: Repository[Any, Any]
    ) -> None:
        """``save`` takes an optional ``command_id``."""
        pending()

    @pytest.mark.anyio
    async def test_delete_removes_aggregate(
        self, repository: Repository[Any, Any]
    ) -> None:
        """A deleted aggregate is no longer loaded."""
        pending()

    @pytest.mark.anyio
    async def test_delete_of_unknown_id_does_not_raise(
        self, repository: Repository[Any, Any]
    ) -> None:
        """``delete`` is idempotent."""
        pending()
