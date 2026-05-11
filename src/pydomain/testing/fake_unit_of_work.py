"""In-memory Unit of Work for testing."""

from __future__ import annotations

from typing import Any

from pydomain.cqrs.behaviors import UnitOfWork
from pydomain.ddd.domain_event import DomainEvent


class FakeUnitOfWork(UnitOfWork):
    """In-memory Unit of Work for testing.

    Tracks commit/rollback calls and collects domain events from
    aggregates that were seen by the repository.
    """

    def __init__(self, repository: Any | None = None) -> None:
        self._committed = False
        self._rolled_back = False
        self._events: list[DomainEvent] = []
        self._repository = repository

    async def __aenter__(self) -> FakeUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> None:
        if exc_type is not None and not self._rolled_back:
            await self.rollback()

    async def commit(self) -> None:
        self._committed = True
        if self._repository is not None:
            for aggregate in list(self._repository._seen):
                self._events.extend(aggregate.pull_events())

    async def rollback(self) -> None:
        self._rolled_back = True

    def collect_events(self) -> list[DomainEvent]:
        return self._events
