"""In-memory fake of ``ProcessedCommandStore`` for testing."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydomain.cqrs.idempotency import MISSING, ProcessedCommandStore


class FakeProcessedCommandStore(ProcessedCommandStore):
    """In-memory fake implementation of :class:`ProcessedCommandStore`.

    Uses a plain ``dict`` keyed by command UUID. Safe for single-threaded
    async test scenarios — not suitable for multi-threaded or multi-process
    tests.
    """

    def __init__(self) -> None:
        self._store: dict[UUID, Any] = {}

    async def get(self, command_id: UUID) -> Any:
        """Return the cached result, or ``MISSING`` if not found."""
        return self._store.get(command_id, MISSING)

    async def set(self, command_id: UUID, result: Any) -> None:
        """Store *result* keyed by *command_id*."""
        self._store[command_id] = result

    async def contains(self, command_id: UUID) -> bool:
        """Return ``True`` if *command_id* is present."""
        return command_id in self._store
