from __future__ import annotations

from collections.abc import Sequence

from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.exceptions import ConcurrencyError
from pydomain.es.event_store import EventStore
from pydomain.es.event_stream import EventStream
from pydomain.es.exceptions import StreamAlreadyExistsError, StreamNotFoundError


class FakeEventStore(EventStore):
    """In-memory event store for testing.

    Stores raw DomainEvent objects in a dict keyed by aggregate ID.
    No serialization round-trip is performed.
    """

    def __init__(self) -> None:
        self._store: dict[str, list[DomainEvent]] = {}
        self._global_log: list[DomainEvent] = []

    async def append_to_stream(
        self,
        aggregate_id: str,
        events: Sequence[DomainEvent],
        expected_version: int,
    ) -> None:
        stream = self._store.get(aggregate_id)
        if stream is not None and expected_version == 0:
            raise StreamAlreadyExistsError(aggregate_id)
        current_version = len(stream) if stream is not None else 0
        if current_version != expected_version:
            raise ConcurrencyError(
                f"Version mismatch for stream {aggregate_id!r}: "
                f"expected {expected_version}, found {current_version}."
            )
        if stream is None:
            self._store[aggregate_id] = list(events)
        else:
            stream.extend(events)
        self._global_log.extend(events)

    async def read_all(self, from_version: int = 0) -> EventStream:
        """Read all events from the global event log starting at ``from_version``.

        Parameters
        ----------
        from_version:
            Zero-based global offset to start reading from.

        Returns
        -------
        EventStream
            All global events from ``from_version`` onward, with the total
            global event count as ``version``.
        """
        return EventStream(
            events=self._global_log[from_version:],
            version=len(self._global_log),
        )

    async def read_stream(
        self,
        aggregate_id: str,
        from_version: int = 0,
    ) -> EventStream:
        stream = self._store.get(aggregate_id)
        if stream is None:
            raise StreamNotFoundError(aggregate_id)
        return EventStream(events=stream[from_version:], version=len(stream))
