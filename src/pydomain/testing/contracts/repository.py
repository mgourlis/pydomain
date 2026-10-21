"""Contract suite for the ``Repository`` Protocol (ADR-001, ADR-050)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.exceptions import ConcurrencyError
from pydomain.ddd.repository import Repository
from pydomain.testing.contracts._backend import AsyncioBackend

__all__ = ["RepositoryContract"]


def _pending_event_ids(aggregate: AggregateRoot[Any]) -> list[UUID]:
    """Read the ids of the events *aggregate* has not yet handed over.

    Reads the buffer without draining it, so the repository under test still
    receives the events on ``save``.
    """
    pending: list[DomainEvent] = aggregate._pending_events  # pyright: ignore[reportPrivateUsage]
    return [event.event_id for event in pending]


class RepositoryContract(AsyncioBackend):
    """Cases every :class:`~pydomain.ddd.repository.Repository` passes.

    Override the ``repository`` fixture to return a fresh, empty repository for
    each test, :meth:`new_aggregate` to build the aggregates it stores, and
    :meth:`change` to change one. Aggregates are compared by ``id`` and events
    by ``event_id``, so a repository that maps aggregates to rows and back
    passes, whether it stores state or events.
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

    def change(self, aggregate: AggregateRoot[Any]) -> None:
        """Make a domain change to *aggregate* that records a pending event.

        Call a behaviour method, such as ``order.confirm()``, on an aggregate
        :meth:`new_aggregate` built and the repository loaded. Override in
        your subclass.
        """
        raise NotImplementedError(
            "Override `change` to call a behaviour method that records an event."
        )

    @pytest.mark.anyio
    async def test_satisfies_protocol(self, repository: Repository[Any, Any]) -> None:
        """The repository is a ``Repository``."""
        assert isinstance(repository, Repository)

    @pytest.mark.anyio
    async def test_get_by_id_returns_none_for_unknown_id(
        self, repository: Repository[Any, Any]
    ) -> None:
        """Loading an id that was never saved returns ``None``."""
        assert await repository.get_by_id(self.new_aggregate().id) is None

    @pytest.mark.anyio
    async def test_saved_aggregate_is_loaded_by_id(
        self, repository: Repository[Any, Any]
    ) -> None:
        """``get_by_id`` returns the aggregate ``save`` inserted."""
        aggregate = self.new_aggregate()

        await repository.save(aggregate)
        loaded = await repository.get_by_id(aggregate.id)

        assert loaded is not None
        assert type(loaded) is type(aggregate)
        assert loaded.id == aggregate.id

    @pytest.mark.anyio
    async def test_save_drains_pending_events_from_aggregate(
        self, repository: Repository[Any, Any]
    ) -> None:
        """After ``save`` the aggregate has no pending events left."""
        aggregate = self.new_aggregate()

        await repository.save(aggregate)

        assert aggregate.pull_events() == []

    @pytest.mark.anyio
    async def test_pull_events_returns_events_drained_by_save(
        self, repository: Repository[Any, Any]
    ) -> None:
        """``pull_events`` hands over the events ``save`` drained, in order."""
        aggregate = self.new_aggregate()
        expected = _pending_event_ids(aggregate)

        await repository.save(aggregate)

        assert [event.event_id for event in repository.pull_events()] == expected

    @pytest.mark.anyio
    async def test_pull_events_clears_buffer(
        self, repository: Repository[Any, Any]
    ) -> None:
        """A second ``pull_events`` with no save in between returns nothing."""
        await repository.save(self.new_aggregate())

        repository.pull_events()

        assert repository.pull_events() == []

    @pytest.mark.anyio
    async def test_pull_events_collects_events_of_every_save(
        self, repository: Repository[Any, Any]
    ) -> None:
        """Events of several saves accumulate until ``pull_events``."""
        first, second = self.new_aggregate(), self.new_aggregate()
        expected = _pending_event_ids(first) + _pending_event_ids(second)

        await repository.save(first)
        await repository.save(second)

        assert [event.event_id for event in repository.pull_events()] == expected

    @pytest.mark.anyio
    async def test_loaded_aggregate_can_be_saved_again(
        self, repository: Repository[Any, Any]
    ) -> None:
        """Saving the current version of a stored aggregate is an update."""
        aggregate = self.new_aggregate()
        await repository.save(aggregate)
        loaded = await repository.get_by_id(aggregate.id)
        assert loaded is not None

        await repository.save(loaded)

        assert await repository.get_by_id(aggregate.id) is not None

    @pytest.mark.anyio
    async def test_saving_a_stale_copy_raises_concurrency_error(
        self, repository: Repository[Any, Any]
    ) -> None:
        """A lost update is a conflict: the second of two changed copies fails.

        Both copies are loaded at the same version and changed. Saving the
        first moves the stored version on, so saving the second must raise
        ``ConcurrencyError`` instead of overwriting the first change.
        """
        aggregate = self.new_aggregate()
        await repository.save(aggregate)
        first = await repository.get_by_id(aggregate.id)
        second = await repository.get_by_id(aggregate.id)
        assert first is not None
        assert second is not None
        self.change(first)
        self.change(second)
        await repository.save(first)

        with pytest.raises(ConcurrencyError):
            await repository.save(second)

    @pytest.mark.anyio
    async def test_save_accepts_command_id(
        self, repository: Repository[Any, Any]
    ) -> None:
        """``save`` takes an optional ``command_id``."""
        aggregate = self.new_aggregate()

        await repository.save(aggregate, command_id=uuid4())

        assert await repository.get_by_id(aggregate.id) is not None

    @pytest.mark.anyio
    async def test_delete_removes_aggregate(
        self, repository: Repository[Any, Any]
    ) -> None:
        """A deleted aggregate is no longer loaded."""
        aggregate = self.new_aggregate()
        await repository.save(aggregate)

        await repository.delete(aggregate.id)

        assert await repository.get_by_id(aggregate.id) is None

    @pytest.mark.anyio
    async def test_delete_of_unknown_id_does_not_raise(
        self, repository: Repository[Any, Any]
    ) -> None:
        """``delete`` is idempotent."""
        await repository.delete(self.new_aggregate().id)
