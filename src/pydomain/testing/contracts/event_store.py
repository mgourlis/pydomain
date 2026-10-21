"""Contract suite for the ``EventStore`` Protocol (ADR-041)."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest

from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.exceptions import ConcurrencyError
from pydomain.es.event_store import EventStore
from pydomain.es.exceptions import DuplicateCommandError, StreamNotFoundError
from pydomain.testing.contracts._backend import AsyncioBackend

__all__ = ["EventStoreContract"]


class _ContractItemAdded(DomainEvent):
    """Synthetic event used when a subclass does not override ``new_event``."""

    sku: str


def _ids(events: Sequence[DomainEvent]) -> list[UUID]:
    return [event.event_id for event in events]


class EventStoreContract(AsyncioBackend):
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

    @pytest.mark.anyio
    async def test_satisfies_protocol(self, event_store: EventStore) -> None:
        """The store is an ``EventStore``."""
        assert isinstance(event_store, EventStore)

    @pytest.mark.anyio
    async def test_read_stream_raises_for_unknown_stream(
        self, event_store: EventStore
    ) -> None:
        """Reading a stream that does not exist raises ``StreamNotFoundError``."""
        with pytest.raises(StreamNotFoundError):
            await event_store.read_stream(self.new_stream_id())

    @pytest.mark.anyio
    async def test_append_creates_stream_at_expected_version_zero(
        self, event_store: EventStore
    ) -> None:
        """Appending at version ``0`` creates the stream with those events."""
        stream_id = self.new_stream_id()
        events = [self.new_event(), self.new_event()]

        await event_store.append_to_stream(stream_id, events, expected_version=0)
        stream = await event_store.read_stream(stream_id)

        assert _ids(stream.events) == _ids(events)
        assert stream.version == 2

    @pytest.mark.anyio
    async def test_append_at_current_version_extends_stream(
        self, event_store: EventStore
    ) -> None:
        """Appending at the current stream length adds events in order."""
        stream_id = self.new_stream_id()
        first, second = self.new_event(), self.new_event()

        await event_store.append_to_stream(stream_id, [first], expected_version=0)
        await event_store.append_to_stream(stream_id, [second], expected_version=1)
        stream = await event_store.read_stream(stream_id)

        assert _ids(stream.events) == _ids([first, second])
        assert stream.version == 2

    @pytest.mark.anyio
    async def test_append_at_stale_version_raises_concurrency_error(
        self, event_store: EventStore
    ) -> None:
        """An ``expected_version`` behind the stream length is a conflict."""
        stream_id = self.new_stream_id()
        await event_store.append_to_stream(
            stream_id, [self.new_event(), self.new_event()], expected_version=0
        )

        with pytest.raises(ConcurrencyError):
            await event_store.append_to_stream(
                stream_id, [self.new_event()], expected_version=1
            )

    @pytest.mark.anyio
    async def test_append_at_version_zero_to_existing_stream_raises(
        self, event_store: EventStore
    ) -> None:
        """Expecting a new stream when the stream exists is a conflict."""
        stream_id = self.new_stream_id()
        await event_store.append_to_stream(
            stream_id, [self.new_event()], expected_version=0
        )

        with pytest.raises(ConcurrencyError):
            await event_store.append_to_stream(
                stream_id, [self.new_event()], expected_version=0
            )

    @pytest.mark.anyio
    async def test_append_to_missing_stream_at_nonzero_version_raises(
        self, event_store: EventStore
    ) -> None:
        """A stream that does not exist has length ``0``; any other is a conflict."""
        with pytest.raises(ConcurrencyError):
            await event_store.append_to_stream(
                self.new_stream_id(), [self.new_event()], expected_version=3
            )

    @pytest.mark.anyio
    async def test_conflicting_append_leaves_stream_unchanged(
        self, event_store: EventStore
    ) -> None:
        """Events of a rejected append are not stored."""
        stream_id = self.new_stream_id()
        stored = self.new_event()
        await event_store.append_to_stream(stream_id, [stored], expected_version=0)

        with pytest.raises(ConcurrencyError):
            await event_store.append_to_stream(
                stream_id, [self.new_event()], expected_version=0
            )
        stream = await event_store.read_stream(stream_id)

        assert _ids(stream.events) == _ids([stored])
        assert stream.version == 1

    @pytest.mark.anyio
    async def test_read_stream_from_version_returns_tail(
        self, event_store: EventStore
    ) -> None:
        """``from_version`` is a zero-based offset; ``version`` stays the length."""
        stream_id = self.new_stream_id()
        events = [self.new_event(), self.new_event(), self.new_event()]
        await event_store.append_to_stream(stream_id, events, expected_version=0)

        stream = await event_store.read_stream(stream_id, from_version=1)

        assert _ids(stream.events) == _ids(events[1:])
        assert stream.version == 3

    @pytest.mark.anyio
    async def test_duplicate_command_id_raises_duplicate_command_error(
        self, event_store: EventStore
    ) -> None:
        """A ``command_id`` already processed for the stream is rejected.

        ADR-041 says the store raises ``DuplicateCommandError``; the
        ``EventStore`` docstring says it SHOULD. A store that does not
        deduplicate commands fails this case and can override it.
        """
        stream_id = self.new_stream_id()
        command_id = uuid4()
        stored = self.new_event()
        await event_store.append_to_stream(
            stream_id, [stored], expected_version=0, command_id=command_id
        )

        with pytest.raises(DuplicateCommandError):
            await event_store.append_to_stream(
                stream_id,
                [self.new_event()],
                expected_version=1,
                command_id=command_id,
            )
        stream = await event_store.read_stream(stream_id)

        assert _ids(stream.events) == _ids([stored])

    @pytest.mark.anyio
    async def test_command_id_is_scoped_to_its_stream(
        self, event_store: EventStore
    ) -> None:
        """One ``command_id`` may append to two different streams."""
        command_id = uuid4()
        first_stream, second_stream = self.new_stream_id(), self.new_stream_id()

        await event_store.append_to_stream(
            first_stream, [self.new_event()], expected_version=0, command_id=command_id
        )
        await event_store.append_to_stream(
            second_stream,
            [self.new_event()],
            expected_version=0,
            command_id=command_id,
        )

        assert (await event_store.read_stream(second_stream)).version == 1

    @pytest.mark.anyio
    async def test_read_all_of_empty_store_is_empty(
        self, event_store: EventStore
    ) -> None:
        """The global log of a store with no events is empty, at version ``0``."""
        log = await event_store.read_all()

        assert list(log.events) == []
        assert log.version == 0

    @pytest.mark.anyio
    async def test_read_all_returns_events_across_streams_in_append_order(
        self, event_store: EventStore
    ) -> None:
        """The global log interleaves streams in append order."""
        order_a, order_b = self.new_stream_id(), self.new_stream_id()
        a1, b1, a2 = self.new_event(), self.new_event(), self.new_event()

        await event_store.append_to_stream(order_a, [a1], expected_version=0)
        await event_store.append_to_stream(order_b, [b1], expected_version=0)
        await event_store.append_to_stream(order_a, [a2], expected_version=1)
        log = await event_store.read_all()

        assert _ids(log.events) == _ids([a1, b1, a2])
        assert log.version == 3

    @pytest.mark.anyio
    async def test_read_all_from_version_returns_tail(
        self, event_store: EventStore
    ) -> None:
        """``read_all`` skips ``from_version`` events; ``version`` stays the total."""
        events = [self.new_event(), self.new_event(), self.new_event()]
        await event_store.append_to_stream(
            self.new_stream_id(), events[:2], expected_version=0
        )
        await event_store.append_to_stream(
            self.new_stream_id(), events[2:], expected_version=0
        )

        log = await event_store.read_all(from_version=2)

        assert _ids(log.events) == _ids(events[2:])
        assert log.version == 3
