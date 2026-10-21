"""Contract suite for the ``OutboxStore`` Protocol (ADR-063, ADR-065)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from pydomain.cqrs.outbox import OutboxEntry, OutboxStore
from pydomain.testing.contracts._support import pending

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


class OutboxStoreContract:
    """Cases every :class:`~pydomain.cqrs.outbox.OutboxStore` passes.

    Override the ``outbox_store`` fixture to return a fresh, empty store for
    each test. Retry times are a day in the past or a day in the future of
    the real clock, so a store whose clock is the real time passes.

    Concurrent relays and stale claims are adapter concerns the Protocol
    leaves out; this suite does not test them.
    """

    @pytest.fixture
    def outbox_store(self) -> OutboxStore:
        """Return a fresh, empty store. Override in your subclass."""
        raise NotImplementedError(
            "Override the `outbox_store` fixture to return your adapter."
        )

    def test_satisfies_protocol(self, outbox_store: OutboxStore) -> None:
        """The store is an ``OutboxStore``."""
        pending()

    @pytest.mark.anyio
    async def test_fetch_from_empty_store_returns_nothing(
        self, outbox_store: OutboxStore
    ) -> None:
        """An empty outbox has no unpublished entries."""
        pending()

    @pytest.mark.anyio
    async def test_appended_entries_are_fetched_oldest_first(
        self, outbox_store: OutboxStore
    ) -> None:
        """Appended entries come back unpublished, oldest first, field for field."""
        pending()

    @pytest.mark.anyio
    async def test_fetch_returns_at_most_limit_entries(
        self, outbox_store: OutboxStore
    ) -> None:
        """``limit`` caps the batch, keeping the oldest entries."""
        pending()

    @pytest.mark.anyio
    async def test_new_entries_report_zero_attempts(
        self, outbox_store: OutboxStore
    ) -> None:
        """An entry that never failed reports ``attempts == 0``."""
        pending()

    @pytest.mark.anyio
    async def test_published_entries_are_not_fetched(
        self, outbox_store: OutboxStore
    ) -> None:
        """``mark_published`` removes an entry from later fetches."""
        pending()

    @pytest.mark.anyio
    async def test_failed_entry_is_hidden_until_next_attempt(
        self, outbox_store: OutboxStore
    ) -> None:
        """An entry whose next attempt is in the future is not fetched."""
        pending()

    @pytest.mark.anyio
    async def test_failed_entry_is_fetched_once_retry_is_due(
        self, outbox_store: OutboxStore
    ) -> None:
        """Once its next attempt time passes, the entry is fetched again."""
        pending()

    @pytest.mark.anyio
    async def test_each_failure_increments_attempts(
        self, outbox_store: OutboxStore
    ) -> None:
        """Fetched entries carry the number of failed attempts so far."""
        pending()

    @pytest.mark.anyio
    async def test_dead_lettered_entry_is_never_fetched(
        self, outbox_store: OutboxStore
    ) -> None:
        """``mark_dead_lettered`` removes an entry from every later fetch."""
        pending()
