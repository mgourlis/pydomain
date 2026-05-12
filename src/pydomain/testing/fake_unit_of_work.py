"""In-memory Unit of Work for testing."""

from __future__ import annotations

from typing import Any

from pydomain.cqrs.unit_of_work import AbstractUnitOfWork


class FakeUnitOfWork(AbstractUnitOfWork):
    """In-memory Unit of Work for testing.

    Tracks commit/rollback calls and collects domain events from
    aggregates that were seen by the repository.
    """

    def __init__(self, repository: Any | None = None) -> None:
        super().__init__()
        self._repository = repository
        self._rolled_back = False

    async def _flush(self) -> None:
        """Copy repository seen aggregates into self._seen for event collection."""
        if self._repository is not None:
            self._seen = set(self._repository._seen)

    async def rollback(self) -> None:
        self._rolled_back = True
        await super().rollback()
