"""In-memory saga repository for testing."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydomain.cqrs.saga.repository import SagaRepository
from pydomain.cqrs.saga.state import SagaState, SagaStatus
from pydomain.ddd.domain_event import DomainEvent


class FakeSagaRepository(SagaRepository):
    """In-memory saga repository for testing purposes.

    Stores saga states in a ``dict`` keyed by ``state.id``.
    Supports all ``SagaRepository`` query methods.

    Returns deep copies on read and stores deep copies on save
    to prevent mutable-alias bugs in tests.
    """

    def __init__(self) -> None:
        self._store: dict[UUID, SagaState] = {}
        self._collected_events: list[DomainEvent] = []

    async def save(self, state: SagaState) -> None:
        """Upsert the saga state (deep copy) and drain its pending events."""
        self._store[state.id] = state.model_copy(deep=True)
        self._collected_events.extend(state.pull_events())

    async def get_by_id(self, id_: UUID) -> SagaState | None:
        """Return a deep copy of the saga state with the given ID, or ``None``."""
        stored = self._store.get(id_)
        return stored.model_copy(deep=True) if stored is not None else None

    async def find_by_correlation_id(
        self, correlation_id: UUID, saga_type: str
    ) -> SagaState | None:
        """Find a saga state by correlation ID and saga type name."""
        for state in self._store.values():
            if state.correlation_id == correlation_id and state.saga_type == saga_type:
                return state.model_copy(deep=True)
        return None

    async def find_stalled_sagas(self, limit: int = 10) -> list[SagaState]:
        """Return sagas that may have stalled during command dispatch.

        Includes sagas with pending_commands (whether dispatched or not)
        and sagas stuck in COMPENSATING state. These sagas may need
        recovery, cleanup, or compensation dispatch.
        """
        stalled = [
            s.model_copy(deep=True)
            for s in self._store.values()
            if not s.is_terminal
            and (s.pending_commands or s.status == SagaStatus.COMPENSATING)
        ]
        return stalled[:limit]

    async def find_suspended_sagas(self, limit: int = 10) -> list[SagaState]:
        """Return sagas in ``SUSPENDED`` status."""
        suspended = [
            s.model_copy(deep=True)
            for s in self._store.values()
            if s.status == SagaStatus.SUSPENDED
        ]
        return suspended[:limit]

    async def find_expired_suspended_sagas(self, limit: int = 10) -> list[SagaState]:
        """Return suspended sagas whose ``timeout_at`` has passed."""
        now = datetime.now(UTC)
        expired = [
            s.model_copy(deep=True)
            for s in self._store.values()
            if s.status == SagaStatus.SUSPENDED
            and s.timeout_at is not None
            and s.timeout_at <= now
        ]
        return expired[:limit]

    def pull_events(self) -> list[DomainEvent]:
        """Drain and return collected domain events."""
        events = self._collected_events
        self._collected_events = []
        return events
