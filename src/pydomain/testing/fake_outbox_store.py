"""In-memory outbox store for testing."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from pydomain.cqrs.outbox import OutboxEntry

__all__ = ["FakeOutboxStore"]


class FakeOutboxStore:
    """In-memory ``OutboxStore`` for tests.

    Records every append, publish mark, and failure so tests can assert on
    the relay's behaviour without a database.  Delivery is simulated by
    :meth:`fetch_unpublished`, which returns unpublished entries whose
    retry time has passed.

    Parameters
    ----------
    entries:
        Every entry appended, in insertion order.
    published:
        ``message_id`` values marked as published, in publication order.
    failures:
        ``(message_id, error, next_attempt_at)`` tuples recorded by
        :meth:`mark_failed`.
    fail_next_fetch:
        When set, the next :meth:`fetch_unpublished` call raises. Used to
        simulate a storage outage.
    fail_next_append:
        When set, the next :meth:`append` call raises. Used to simulate a
        failed outbox write inside a transaction.
    attempts:
        Failed delivery attempts per ``message_id``, incremented by
        :meth:`mark_failed` and surfaced on :meth:`fetch_unpublished`.
    dead_lettered:
        ``message_id`` to final error, recorded by :meth:`mark_dead_lettered`.
        Dead-lettered entries are never fetched again.
    clock:
        Injectable time source, so tests can control when a retry becomes
        due. Defaults to the real UTC clock.
    """

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self.entries: list[OutboxEntry] = []
        self.published: list[str] = []
        self.failures: list[tuple[str, str, datetime]] = []
        self.attempts: dict[str, int] = {}
        self.dead_lettered: dict[str, str] = {}
        self.fail_next_fetch = False
        self.fail_next_append = False
        self._clock = clock or (lambda: datetime.now(UTC))
        self._published_ids: set[str] = set()
        self._retry_at: dict[str, datetime] = {}

    async def append(self, entries: Sequence[OutboxEntry]) -> None:
        """Record entries as unpublished.

        Raises
        ------
        RuntimeError
            If ``fail_next_append`` is set. The flag is cleared first, so
            a single failure is injected.
        """
        if self.fail_next_append:
            self.fail_next_append = False
            msg = "FakeOutboxStore: injected append failure"
            raise RuntimeError(msg)
        self.entries.extend(entries)

    async def fetch_unpublished(self, limit: int = 100) -> list[OutboxEntry]:
        """Return unpublished entries whose retry time has passed.

        Raises
        ------
        RuntimeError
            If ``fail_next_fetch`` is set. The flag is cleared first, so
            a single failure is injected.
        """
        if self.fail_next_fetch:
            self.fail_next_fetch = False
            msg = "FakeOutboxStore: injected fetch failure"
            raise RuntimeError(msg)

        now = self._clock()
        ready: list[OutboxEntry] = []
        for entry in self.entries:
            if entry.message_id in self._published_ids:
                continue
            if entry.message_id in self.dead_lettered:
                continue
            retry_at = self._retry_at.get(entry.message_id)
            if retry_at is not None and retry_at > now:
                continue
            ready.append(
                entry.model_copy(
                    update={"attempts": self.attempts.get(entry.message_id, 0)}
                )
            )
            if len(ready) == limit:
                break
        return ready

    async def mark_published(self, message_ids: Sequence[str]) -> None:
        """Mark ``message_ids`` as published."""
        for message_id in message_ids:
            self.published.append(message_id)
            self._published_ids.add(message_id)

    async def mark_failed(
        self,
        message_id: str,
        *,
        error: str,
        next_attempt_at: datetime,
    ) -> None:
        """Record a delivery failure, increment attempts, and set the retry time."""
        self.failures.append((message_id, error, next_attempt_at))
        self._retry_at[message_id] = next_attempt_at
        self.attempts[message_id] = self.attempts.get(message_id, 0) + 1

    async def mark_dead_lettered(self, message_id: str, *, error: str) -> None:
        """Record the entry as dead-lettered. It is never fetched again."""
        self.dead_lettered[message_id] = error
        self.attempts[message_id] = self.attempts.get(message_id, 0) + 1

    def is_published(self, message_id: str) -> bool:
        """Return ``True`` if *message_id* was marked published."""
        return message_id in self._published_ids

    def is_dead_lettered(self, message_id: str) -> bool:
        """Return ``True`` if *message_id* was dead-lettered."""
        return message_id in self.dead_lettered
