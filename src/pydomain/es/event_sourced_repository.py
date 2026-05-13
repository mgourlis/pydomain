from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydomain.es.aggregate import EventSourcedAggregateRoot


@runtime_checkable
class EventSourcedRepository[T: EventSourcedAggregateRoot[Any], TId](Protocol):
    """Protocol for repositories that persist event-sourced aggregates.

    Implementations:
    * ``save(aggregate)`` drains pending events and appends them to the
      event store with optimistic concurrency control.
    * ``get_by_id(id_)`` reads the stream, creates a fresh aggregate
      instance, and replays all events to rebuild current state.
    """

    async def save(self, aggregate: T) -> None:
        """Persist pending events from the aggregate.

        Implementation:
        1. Pull pending events from the aggregate via ``pull_events()``
        2. If no events, return
        3. Calculate ``expected_version = aggregate.version - len(events)``
        4. Call
           ``event_store.append_to_stream(str(aggregate.id), events, expected_version)``
        """
        ...

    async def get_by_id(self, id_: TId) -> T | None:
        """Load an aggregate by replaying its event stream.

        Implementation:
        1. Call ``event_store.read_stream(str(id_))``
        2. If ``StreamNotFoundError``, return ``None``
        3. Create a fresh aggregate instance: ``cls(id=id_)``
        4. For each event, call ``aggregate._replay(event)``
        5. Return the reconstituted aggregate
        """
        ...
