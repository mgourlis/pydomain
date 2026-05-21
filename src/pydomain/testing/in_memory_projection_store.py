"""In-memory projection store for testing."""

from __future__ import annotations

from typing import Any

from pydomain.cqrs.projection import ProjectionStore

__all__ = [
    "InMemoryProjectionStore",
]


class InMemoryProjectionStore(ProjectionStore):
    """In-memory projection store for testing purposes.

    Stores read model state in a ``dict`` keyed by projection identity.
    Useful for unit tests and integration tests that do not require a
    real database.

    Example::

        store = InMemoryProjectionStore()
        await store.save("orders-summary", {"total": 42})
        state = await store.load("orders-summary")
    """

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    async def load(self, projection_id: str) -> Any | None:
        """Load the persisted state for a projection."""
        return self._store.get(projection_id)

    async def save(self, projection_id: str, state: Any) -> None:
        """Persist projection state."""
        self._store[projection_id] = state
