"""In-memory outbox store for testing."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from pydomain.cqrs.exceptions import CQRSError
from pydomain.cqrs.outbox import OutboxEntry

__all__ = ["FakeOutboxStore"]


class FakeOutboxStore:
    """In-memory ``OutboxStore`` for tests.

    Records every append, publish mark, and failure so tests can assert on
    the relay's behaviour without a database.  Delivery is simulated by
    :meth:`fetch_unpublished`, which returns unpublished entries whose
    retry time has passed and **claims** them: a later fetch does not
    return a claimed entry until it is marked, or until ``claim_timeout``
    has passed on the store's clock, as when a relay crashed mid-pass
    (ADR-074). ``mark_failed`` releases the claim; the entry then waits for
    its ``next_attempt_at``.

    Inspecting the outbox through ``fetch_unpublished`` therefore claims
    what it returns; read :attr:`entries`, :attr:`published` and the other
    records instead.

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
        due and when a claim goes stale. Defaults to the real UTC clock.
    claim_timeout:
        How long a fetched entry stays claimed if it is never marked.
        Defaults to five minutes; must be positive.

    Raises
    ------
    ValueError
        If ``claim_timeout`` is zero or negative.
    """

    def __init__(
        self,
        clock: Callable[[], datetime] | None = None,
        claim_timeout: timedelta = timedelta(minutes=5),
    ) -> None:
        if claim_timeout <= timedelta(0):
            msg = "claim_timeout must be positive"
            raise ValueError(msg)
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
        self._claim_timeout = claim_timeout
        self._claimed_until: dict[str, datetime] = {}

    async def append(self, entries: Sequence[OutboxEntry]) -> None:
        """Record entries as unpublished.

        The whole batch is checked before anything is stored.

        Raises
        ------
        CQRSError
            If a ``message_id`` is already stored or repeats within the
            batch; nothing of the batch is stored.
        RuntimeError
            If ``fail_next_append`` is set. The flag is cleared first, so
            a single failure is injected.
        """
        if self.fail_next_append:
            self.fail_next_append = False
            msg = "FakeOutboxStore: injected append failure"
            raise RuntimeError(msg)
        seen = {entry.message_id for entry in self.entries}
        for entry in entries:
            if entry.message_id in seen:
                msg = f"FakeOutboxStore: duplicate message_id {entry.message_id}"
                raise CQRSError(msg)
            seen.add(entry.message_id)
        self.entries.extend(entries)

    async def fetch_unpublished(self, limit: int = 100) -> list[OutboxEntry]:
        """Return and claim unpublished entries whose retry time has passed.

        Entries claimed by an earlier fetch are skipped until they are
        marked or their claim goes stale.

        Raises
        ------
        ValueError
            If ``limit`` is below ``1``.
        RuntimeError
            If ``fail_next_fetch`` is set. The flag is cleared first, so
            a single failure is injected.
        """
        if type(limit) is not int or limit < 1:
            msg = "limit must be an int of at least 1"
            raise ValueError(msg)
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
            claimed_until = self._claimed_until.get(entry.message_id)
            if claimed_until is not None and claimed_until > now:
                continue
            self._claimed_until[entry.message_id] = now + self._claim_timeout
            ready.append(
                entry.model_copy(
                    update={"attempts": self.attempts.get(entry.message_id, 0)}
                )
            )
            if len(ready) == limit:
                break
        return ready

    async def mark_published(self, message_ids: Sequence[str]) -> None:
        """Mark ``message_ids`` as published.

        A published entry wins in either order: an entry already
        dead-lettered moves to published, because the broker did receive it
        (ADR-074).
        """
        for message_id in message_ids:
            self.published.append(message_id)
            self._published_ids.add(message_id)
            self.dead_lettered.pop(message_id, None)
            self._claimed_until.pop(message_id, None)

    async def mark_failed(
        self,
        message_id: str,
        *,
        error: str,
        next_attempt_at: datetime,
    ) -> None:
        """Record a failure, count the attempt, set the retry time, drop the claim.

        A no-op for an entry already marked published: a publication wins
        over a later failure from a relay whose claim had gone stale
        (ADR-074).
        """
        if message_id in self._published_ids:
            return
        self.failures.append((message_id, error, next_attempt_at))
        self._claimed_until.pop(message_id, None)
        self._retry_at[message_id] = next_attempt_at
        self.attempts[message_id] = self.attempts.get(message_id, 0) + 1

    async def mark_dead_lettered(self, message_id: str, *, error: str) -> None:
        """Record the entry as dead-lettered. It is never fetched again.

        A no-op for an entry already marked published (ADR-074).
        """
        if message_id in self._published_ids:
            return
        self.dead_lettered[message_id] = error
        self._claimed_until.pop(message_id, None)
        self.attempts[message_id] = self.attempts.get(message_id, 0) + 1

    def is_published(self, message_id: str) -> bool:
        """Return ``True`` if *message_id* was marked published."""
        return message_id in self._published_ids

    def is_dead_lettered(self, message_id: str) -> bool:
        """Return ``True`` if *message_id* was dead-lettered."""
        return message_id in self.dead_lettered
