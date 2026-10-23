"""Contract suite for the ``OutboxStore`` Protocol (ADR-063, ADR-065)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from pydomain.cqrs.exceptions import CQRSError
from pydomain.cqrs.outbox import OutboxEntry, OutboxStore
from pydomain.testing.contracts._backend import AsyncioBackend

__all__ = ["OutboxStoreContract"]

_BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def _entry(n: int) -> OutboxEntry:
    """Return a synthetic entry; a higher *n* occurred later."""
    return OutboxEntry(
        message_id=str(uuid4()),
        topic="contract.orders.placed",
        payload={"order_id": f"order-{n}", "line_count": n},
        occurred_at=_BASE_TIME + timedelta(seconds=n),
        correlation_id=str(uuid4()),
        causation_id=str(uuid4()),
    )


def _ids(entries: Sequence[OutboxEntry]) -> list[str]:
    return [entry.message_id for entry in entries]


def _past() -> datetime:
    return datetime.now(UTC) - timedelta(days=1)


def _future() -> datetime:
    return datetime.now(UTC) + timedelta(days=1)


class OutboxStoreContract(AsyncioBackend):
    """Cases every :class:`~pydomain.cqrs.outbox.OutboxStore` passes.

    Override the ``outbox_store`` fixture to return a fresh, empty store for
    each test. Retry times are a day in the past or a day in the future of
    the real clock, so a store whose clock is the real time passes.

    A fetch claims what it returns: a second fetch before any mark does not
    return the same entries (ADR-074). How long a claim lasts before it goes
    stale is an adapter detail the Protocol leaves out; this suite does not
    test it.
    """

    @pytest.fixture
    def outbox_store(self) -> OutboxStore:
        """Return a fresh, empty store. Override in your subclass."""
        raise NotImplementedError(
            "Override the `outbox_store` fixture to return your adapter."
        )

    @pytest.mark.anyio
    async def test_satisfies_protocol(self, outbox_store: OutboxStore) -> None:
        """The store is an ``OutboxStore``."""
        assert isinstance(outbox_store, OutboxStore)

    @pytest.mark.anyio
    async def test_fetch_from_empty_store_returns_nothing(
        self, outbox_store: OutboxStore
    ) -> None:
        """An empty outbox has no unpublished entries."""
        assert await outbox_store.fetch_unpublished() == []

    @pytest.mark.anyio
    async def test_appended_entries_are_fetched_oldest_first(
        self, outbox_store: OutboxStore
    ) -> None:
        """Appended entries come back unpublished, oldest first, field for field."""
        entries = [_entry(1), _entry(2), _entry(3)]

        await outbox_store.append(entries)
        fetched = await outbox_store.fetch_unpublished()

        assert _ids(fetched) == _ids(entries)
        for got, sent in zip(fetched, entries, strict=True):
            assert got.topic == sent.topic
            assert got.payload == sent.payload
            assert got.event_version == sent.event_version
            assert got.occurred_at == sent.occurred_at
            assert got.correlation_id == sent.correlation_id
            assert got.causation_id == sent.causation_id

    @pytest.mark.anyio
    async def test_fetch_returns_at_most_limit_entries(
        self, outbox_store: OutboxStore
    ) -> None:
        """``limit`` caps the batch, keeping the oldest entries."""
        entries = [_entry(1), _entry(2), _entry(3)]
        await outbox_store.append(entries)

        fetched = await outbox_store.fetch_unpublished(limit=2)

        assert _ids(fetched) == _ids(entries[:2])

    @pytest.mark.anyio
    async def test_new_entries_report_zero_attempts(
        self, outbox_store: OutboxStore
    ) -> None:
        """An entry that never failed reports ``attempts == 0``."""
        await outbox_store.append([_entry(1)])

        (fetched,) = await outbox_store.fetch_unpublished()

        assert fetched.attempts == 0

    @pytest.mark.anyio
    async def test_published_entries_are_not_fetched(
        self, outbox_store: OutboxStore
    ) -> None:
        """``mark_published`` removes an entry from later fetches."""
        published, pending = _entry(1), _entry(2)
        await outbox_store.append([published, pending])

        await outbox_store.mark_published([published.message_id])

        assert _ids(await outbox_store.fetch_unpublished()) == [pending.message_id]

    @pytest.mark.anyio
    async def test_failed_entry_is_hidden_until_next_attempt(
        self, outbox_store: OutboxStore
    ) -> None:
        """An entry whose next attempt is in the future is not fetched."""
        entry = _entry(1)
        await outbox_store.append([entry])

        await outbox_store.mark_failed(
            entry.message_id, error="broker unavailable", next_attempt_at=_future()
        )

        assert await outbox_store.fetch_unpublished() == []

    @pytest.mark.anyio
    async def test_failed_entry_is_fetched_once_retry_is_due(
        self, outbox_store: OutboxStore
    ) -> None:
        """Once its next attempt time passes, the entry is fetched again."""
        entry = _entry(1)
        await outbox_store.append([entry])

        await outbox_store.mark_failed(
            entry.message_id, error="broker unavailable", next_attempt_at=_past()
        )

        assert _ids(await outbox_store.fetch_unpublished()) == [entry.message_id]

    @pytest.mark.anyio
    async def test_each_failure_increments_attempts(
        self, outbox_store: OutboxStore
    ) -> None:
        """Fetched entries carry the number of failed attempts so far."""
        entry = _entry(1)
        await outbox_store.append([entry])

        await outbox_store.mark_failed(
            entry.message_id, error="broker unavailable", next_attempt_at=_past()
        )
        await outbox_store.mark_failed(
            entry.message_id, error="broker unavailable", next_attempt_at=_past()
        )
        (fetched,) = await outbox_store.fetch_unpublished()

        assert fetched.attempts == 2

    @pytest.mark.anyio
    async def test_dead_lettered_entry_is_never_fetched(
        self, outbox_store: OutboxStore
    ) -> None:
        """``mark_dead_lettered`` removes an entry from every later fetch."""
        dead, pending = _entry(1), _entry(2)
        await outbox_store.append([dead, pending])

        await outbox_store.mark_dead_lettered(dead.message_id, error="gave up")

        assert _ids(await outbox_store.fetch_unpublished()) == [pending.message_id]

    @pytest.mark.anyio
    @pytest.mark.parametrize("limit", [0, -1])
    async def test_fetch_with_non_positive_limit_raises_value_error(
        self, outbox_store: OutboxStore, limit: int
    ) -> None:
        """A ``limit`` below ``1`` raises ``ValueError`` (ADR-074)."""
        await outbox_store.append([_entry(1)])

        with pytest.raises(ValueError):
            await outbox_store.fetch_unpublished(limit=limit)

    @pytest.mark.anyio
    async def test_second_fetch_before_any_mark_returns_other_entries(
        self, outbox_store: OutboxStore
    ) -> None:
        """A fetched entry is claimed: a second fetch does not return it (ADR-074)."""
        entries = [_entry(1), _entry(2), _entry(3)]
        await outbox_store.append(entries)

        first = await outbox_store.fetch_unpublished(limit=2)
        second = await outbox_store.fetch_unpublished()

        assert _ids(first) == _ids(entries[:2])
        assert _ids(second) == _ids(entries[2:])

    @pytest.mark.anyio
    async def test_failed_entry_is_released_from_its_claim(
        self, outbox_store: OutboxStore
    ) -> None:
        """``mark_failed`` releases a claimed entry through ``next_attempt_at``."""
        entry = _entry(1)
        await outbox_store.append([entry])
        await outbox_store.fetch_unpublished()

        await outbox_store.mark_failed(
            entry.message_id, error="broker unavailable", next_attempt_at=_past()
        )

        assert _ids(await outbox_store.fetch_unpublished()) == [entry.message_id]

    @pytest.mark.anyio
    async def test_marks_after_publication_leave_the_entry_published(
        self, outbox_store: OutboxStore
    ) -> None:
        """A later ``mark_failed`` or ``mark_dead_lettered`` is a no-op (ADR-074)."""
        entry = _entry(1)
        await outbox_store.append([entry])
        await outbox_store.mark_published([entry.message_id])

        await outbox_store.mark_failed(
            entry.message_id, error="late failure", next_attempt_at=_past()
        )
        await outbox_store.mark_dead_lettered(entry.message_id, error="late failure")

        assert await outbox_store.fetch_unpublished() == []

    @pytest.mark.anyio
    async def test_publication_after_a_dead_letter_keeps_the_entry_unfetched(
        self, outbox_store: OutboxStore
    ) -> None:
        """A dead-lettered entry later marked published is published (ADR-074)."""
        entry = _entry(1)
        await outbox_store.append([entry])
        await outbox_store.mark_dead_lettered(entry.message_id, error="gave up")

        await outbox_store.mark_published([entry.message_id])
        await outbox_store.mark_failed(
            entry.message_id, error="late failure", next_attempt_at=_past()
        )

        assert await outbox_store.fetch_unpublished() == []

    @pytest.mark.anyio
    async def test_append_refuses_a_duplicate_message_id(
        self, outbox_store: OutboxStore
    ) -> None:
        """A second entry with a stored ``message_id`` raises ``CQRSError`` (ADR-080).

        The stored entry is unchanged.
        """
        entry = _entry(1)
        duplicate = entry.model_copy(update={"payload": {"order_id": "other"}})
        await outbox_store.append([entry])

        with pytest.raises(CQRSError):
            await outbox_store.append([duplicate])

        fetched = await outbox_store.fetch_unpublished()
        assert _ids(fetched) == [entry.message_id]
        assert fetched[0].payload == entry.payload

    @pytest.mark.anyio
    async def test_append_refuses_a_batch_that_repeats_a_message_id(
        self, outbox_store: OutboxStore
    ) -> None:
        """A batch that repeats a ``message_id`` raises and stores nothing of it."""
        first, second = _entry(1), _entry(2)

        with pytest.raises(CQRSError):
            await outbox_store.append([first, second, first])

        assert await outbox_store.fetch_unpublished() == []
