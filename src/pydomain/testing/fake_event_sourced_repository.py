from __future__ import annotations

from pydomain.es.aggregate import EventSourcedAggregateRoot
from pydomain.es.event_sourced_repository import EventSourcedRepository
from pydomain.es.event_store import EventStore
from pydomain.es.exceptions import StreamNotFoundError


class FakeEventSourcedRepository[T: EventSourcedAggregateRoot, TId](
    EventSourcedRepository[T, TId]
):
    """In-memory fake for ``EventSourcedRepository``.

    Uses any ``EventStore`` implementation (typically ``FakeEventStore``
    in tests) and an aggregate class reference to implement save/load
    via event replay.
    """

    def __init__(self, event_store: EventStore, aggregate_cls: type[T]) -> None:
        self._event_store = event_store
        self._aggregate_cls = aggregate_cls

    async def save(self, aggregate: T) -> None:
        events = aggregate.pull_events()
        if not events:
            return
        expected_version = aggregate.version - len(events)
        await self._event_store.append_to_stream(
            str(aggregate.id), events, expected_version
        )

    async def get_by_id(self, id_: TId) -> T | None:
        aggregate_id = str(id_)
        try:
            stream = await self._event_store.read_stream(aggregate_id)
        except StreamNotFoundError:
            return None
        aggregate = self._aggregate_cls(id=id_)
        for event in stream.events:
            aggregate._replay(event)
        return aggregate
