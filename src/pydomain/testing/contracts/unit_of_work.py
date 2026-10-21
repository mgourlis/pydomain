"""Contract suite for the ``UnitOfWork`` Protocol (ADR-005, ADR-020)."""

from __future__ import annotations

import pytest

from pydomain.cqrs.unit_of_work import UnitOfWork
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing.contracts._backend import AsyncioBackend

__all__ = ["UnitOfWorkContract"]


class _HandlerFailed(Exception):
    """Raised inside a unit of work to simulate a failing handler."""


class UnitOfWorkContract(AsyncioBackend):
    """Cases every :class:`~pydomain.cqrs.unit_of_work.UnitOfWork` passes.

    Override the ``unit_of_work`` fixture to return a fresh unit of work, over
    empty storage, for each test, and :meth:`stage_change` to make a change
    through it the way a command handler would.
    """

    @pytest.fixture
    def unit_of_work(self) -> UnitOfWork:
        """Return a fresh unit of work. Override in your subclass."""
        raise NotImplementedError(
            "Override the `unit_of_work` fixture to return your adapter."
        )

    async def stage_change(self, uow: UnitOfWork) -> int:
        """Make a change through *uow* that records domain events.

        Called inside ``async with uow``, as a command handler runs. Typically
        it creates an aggregate and saves it through one of the unit of
        work's repositories. Return the number of domain events the change
        records. Override in your subclass.
        """
        raise NotImplementedError(
            "Override `stage_change` to save an aggregate through the unit of work."
        )

    @pytest.mark.anyio
    async def test_satisfies_protocol(self, unit_of_work: UnitOfWork) -> None:
        """The unit of work is a ``UnitOfWork``."""
        assert isinstance(unit_of_work, UnitOfWork)

    @pytest.mark.anyio
    async def test_context_manager_yields_a_unit_of_work(
        self, unit_of_work: UnitOfWork
    ) -> None:
        """``async with`` hands the caller a ``UnitOfWork``."""
        async with unit_of_work as entered:
            assert isinstance(entered, UnitOfWork)

    @pytest.mark.anyio
    async def test_commit_collects_staged_events(
        self, unit_of_work: UnitOfWork
    ) -> None:
        """After ``commit``, ``collect_events`` returns the change's events."""
        async with unit_of_work as uow:
            recorded = await self.stage_change(uow)
            await uow.commit()

        events = uow.collect_events()

        assert len(events) == recorded
        assert all(isinstance(event, DomainEvent) for event in events)

    @pytest.mark.anyio
    async def test_error_inside_context_propagates_without_collecting_events(
        self, unit_of_work: UnitOfWork
    ) -> None:
        """An error before ``commit`` propagates, and no event is collected."""
        with pytest.raises(_HandlerFailed):
            async with unit_of_work as uow:
                await self.stage_change(uow)
                raise _HandlerFailed

        assert uow.collect_events() == []

    @pytest.mark.anyio
    async def test_exit_without_commit_collects_no_events(
        self, unit_of_work: UnitOfWork
    ) -> None:
        """Events are collected only by a commit; leaving without one has none."""
        async with unit_of_work as uow:
            await self.stage_change(uow)

        assert uow.collect_events() == []
