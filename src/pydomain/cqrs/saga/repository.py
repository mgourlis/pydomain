"""SagaRepository protocol — saga-specific persistence contract."""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from pydomain.cqrs.saga.state import SagaState
from pydomain.ddd.domain_event import DomainEvent

__all__ = [
    "SagaRepository",
]


@runtime_checkable
class SagaRepository(Protocol):
    """Repository protocol for saga state persistence.

    Extends the standard repository concept with saga-specific queries
    for correlation-id lookup, stalled-saga recovery, and suspended-saga
    timeout handling.
    """

    async def save(self, state: SagaState) -> None:
        """Persist a ``SagaState`` (insert or update).

        Drains pending domain events from the state and stores them
        in an internal buffer for later retrieval via ``pull_events()``.

        Args:
            state: The saga state to persist.

        Raises:
            ConcurrencyError: when the expected version does not match.
        """
        ...

    async def get_by_id(self, id_: UUID) -> SagaState | None:
        """Retrieve a ``SagaState`` by its identity, or ``None``."""
        ...

    async def find_by_correlation_id(
        self, correlation_id: UUID, saga_type: str
    ) -> SagaState | None:
        """Find a saga state by correlation ID and saga type name.

        Used by the ``SagaManager`` to locate an existing saga instance
        for a given correlation chain.
        """
        ...

    async def find_stalled_sagas(self, limit: int = 10) -> list[SagaState]:
        """Return sagas that may have stalled during command dispatch.

        These are sagas with non-empty ``pending_commands`` that have
        not been fully dispatched.
        """
        ...

    async def find_suspended_sagas(self, limit: int = 10) -> list[SagaState]:
        """Return sagas in ``SUSPENDED`` status (for timeout checks)."""
        ...

    async def find_expired_suspended_sagas(self, limit: int = 10) -> list[SagaState]:
        """Return suspended sagas whose ``timeout_at`` has passed."""
        ...

    def pull_events(self) -> list[DomainEvent]:
        """Drain and return collected domain events.

        Returns all events collected by ``save()`` since the last call
        to ``pull_events()``, then clears the internal buffer.
        """
        ...
