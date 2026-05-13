"""In-memory event store for testing."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from pydomain.cqrs.event_store import EventStore
from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.exceptions import ConcurrencyError
from pydomain.infrastructure.event_registry import EventRegistry

__all__ = [
    "InMemoryEventStore",
]


class InMemoryEventStore(EventStore):
    """In-memory event store for testing purposes.

    Stores serialized events in a ``dict`` keyed by stream (aggregate)
    identity.  Each stream entry is a list of
    ``(version, serialized_data)`` tuples.

    Example::

        registry = EventRegistry()
        registry.register(OrderPlaced)
        store = InMemoryEventStore(registry)

        await store.append_events(order.id, [OrderPlaced(...)], expected_version=0)
        events = await store.read_events(order.id)
    """

    def __init__(self, event_registry: EventRegistry) -> None:
        self._event_registry = event_registry
        self._store: dict[UUID, list[tuple[int, dict[str, Any]]]] = {}

    async def append_events(
        self,
        stream_id: UUID,
        events: Sequence[DomainEvent],
        expected_version: int,
    ) -> None:
        """Append events to the stream with optimistic concurrency check.

        Parameters
        ----------
        stream_id:
            The aggregate / stream identity.
        events:
            The domain events to persist.
        expected_version:
            The number of events currently in the stream.  Must be
            ``0`` for a new stream.

        Raises
        ------
        ConcurrencyError
            If the current stream length does not match
            ``expected_version``.
        """
        stream = self._store.setdefault(stream_id, [])
        if len(stream) != expected_version:
            raise ConcurrencyError(
                f"Version mismatch for stream {stream_id!r}: "
                f"expected {expected_version}, found {len(stream)}."
            )
        for i, event in enumerate(events):
            version = expected_version + i + 1
            serialized = self._event_registry.serialize(event)
            stream.append((version, serialized))

    async def read_events(self, stream_id: UUID) -> list[DomainEvent]:
        """Return all events for a stream ordered by version.

        Parameters
        ----------
        stream_id:
            The aggregate / stream identity.

        Returns
        -------
        list[DomainEvent]
            The deserialized events in version order, or an empty list
            if the stream is unknown.  Unregistered event types are
            returned as ``GenericDomainEvent`` instances (weak-schema
            fallback).
        """
        stream = self._store.get(stream_id, [])
        result: list[DomainEvent] = []
        for _version, serialized in sorted(stream, key=lambda item: item[0]):
            event = self._event_registry.deserialize(serialized)
            result.append(event)  # type: ignore[arg-type]
        return result
