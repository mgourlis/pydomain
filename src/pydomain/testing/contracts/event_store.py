"""Contract suite for the ``EventStore`` Protocol (ADR-041)."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest

from pydomain.ddd.domain_event import DomainEvent
from pydomain.es.event_store import EventStore
from pydomain.testing.contracts._support import pending

__all__ = ["EventStoreContract"]


class _ContractItemAdded(DomainEvent):
    """Synthetic event used when a subclass does not override ``new_event``."""

    sku: str


def _ids(events: Sequence[DomainEvent]) -> list[UUID]:
    return [event.event_id for event in events]


class EventStoreContract:
    """Cases every :class:`~pydomain.es.event_store.EventStore` passes.

    Override the ``event_store`` fixture to return a fresh, empty store for
    each test. Events are compared by ``event_id``, so a store that
    serializes and rehydrates events passes.

    Override :meth:`new_event` when your store can persist only event types it
    knows, and :meth:`new_stream_id` when it expects a particular id format.
    """

    @pytest.fixture
    def event_store(self) -> EventStore:
        """Return a fresh, empty store. Override in your subclass."""
        raise NotImplementedError(
            "Override the `event_store` fixture to return your adapter."
        )

    def new_event(self) -> DomainEvent:
        """Return a new domain event your store can persist."""
        return _ContractItemAdded(sku="SKU-1")

    def new_stream_id(self) -> str:
        """Return the id of a stream that does not exist yet."""
        return f"contract-order-{uuid4()}"

    def test_satisfies_protocol(self, event_store: EventStore) -> None:
        """The store is an ``EventStore``."""
        pending()

    @pytest.mark.anyio
    async def test_read_stream_raises_for_unknown_stream(
        self, event_store: EventStore
    ) -> None:
        """Reading a stream that does not exist raises ``StreamNotFoundError``."""
        pending()

    @pytest.mark.anyio
    async def test_append_creates_stream_at_expected_version_zero(
        self, event_store: EventStore
    ) -> None:
        """Appending at version ``0`` creates the stream with those events."""
        pending()

    @pytest.mark.anyio
    async def test_append_at_current_version_extends_stream(
        self, event_store: EventStore
    ) -> None:
        """Appending at the current stream length adds events in order."""
        pending()

    @pytest.mark.anyio
    async def test_append_at_stale_version_raises_concurrency_error(
        self, event_store: EventStore
    ) -> None:
        """An ``expected_version`` behind the stream length is a conflict."""
        pending()

    @pytest.mark.anyio
    async def test_append_at_version_zero_to_existing_stream_raises(
        self, event_store: EventStore
    ) -> None:
        """Expecting a new stream when the stream exists is a conflict."""
        pending()

    @pytest.mark.anyio
    async def test_append_to_missing_stream_at_nonzero_version_raises(
        self, event_store: EventStore
    ) -> None:
        """A stream that does not exist has length ``0``; any other is a conflict."""
        pending()

    @pytest.mark.anyio
    async def test_conflicting_append_leaves_stream_unchanged(
        self, event_store: EventStore
    ) -> None:
        """Events of a rejected append are not stored."""
        pending()

    @pytest.mark.anyio
    async def test_read_stream_from_version_returns_tail(
        self, event_store: EventStore
    ) -> None:
        """``from_version`` is a zero-based offset; ``version`` stays the length."""
        pending()

    @pytest.mark.anyio
    async def test_duplicate_command_id_raises_duplicate_command_error(
        self, event_store: EventStore
    ) -> None:
        """A ``command_id`` already processed for the stream is rejected."""
        pending()

    @pytest.mark.anyio
    async def test_command_id_is_scoped_to_its_stream(
        self, event_store: EventStore
    ) -> None:
        """One ``command_id`` may append to two different streams."""
        pending()

    @pytest.mark.anyio
    async def test_read_all_of_empty_store_is_empty(
        self, event_store: EventStore
    ) -> None:
        """The global log of a store with no events is empty, at version ``0``."""
        pending()

    @pytest.mark.anyio
    async def test_read_all_returns_events_across_streams_in_append_order(
        self, event_store: EventStore
    ) -> None:
        """The global log interleaves streams in append order."""
        pending()

    @pytest.mark.anyio
    async def test_read_all_from_version_returns_tail(
        self, event_store: EventStore
    ) -> None:
        """``read_all`` skips ``from_version`` events; ``version`` stays the total."""
        pending()
