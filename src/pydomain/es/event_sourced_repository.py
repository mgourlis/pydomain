from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydomain.es.aggregate import EventSourcedAggregateRoot
from pydomain.es.snapshot import SnapshotPolicy, SnapshotStore


@runtime_checkable
class EventSourcedRepository[T: EventSourcedAggregateRoot[Any], TId](Protocol):
    """Protocol for repositories that persist event-sourced aggregates.

    Implementations:
    * ``save(aggregate, snapshot_store, snapshot_policy)`` drains pending
      events, appends them to the event store with optimistic concurrency
      control, and optionally takes a snapshot if the policy dictates.
    * ``get_by_id(id_, snapshot_store)`` reads the event stream to rebuild
      aggregate state, optionally using a snapshot as a starting point
      for faster hydration.
    """

    @property
    def aggregate_type(self) -> str:
        """The type discriminator for this repository's aggregate.

        Used as the ``aggregate_type`` parameter when interacting with a
        :class:`SnapshotStore`.  Typical implementations derive this from
        the aggregate class name (e.g. ``self._aggregate_cls.__name__``).
        """
        ...

    async def save(
        self,
        aggregate: T,
        snapshot_store: SnapshotStore | None = None,
        snapshot_policy: SnapshotPolicy | None = None,
    ) -> None:
        """Persist pending events and optionally take a snapshot.

        Implementation:
        1. Pull pending events via ``pull_events()``
        2. If no events, return
        3. Calculate ``expected_version = aggregate.version - len(events)``
        4. Call
           ``event_store.append_to_stream(str(aggregate.id), events, expected_version)``
        5. If both *snapshot_store* and *snapshot_policy* are provided,
           evaluate ``snapshot_policy.should_snapshot(...)``.  If True,
           call ``aggregate._take_snapshot()`` and
           ``snapshot_store.save(self.aggregate_type, snapshot)``.
        """
        ...

    async def get_by_id(
        self,
        id_: TId,
        snapshot_store: SnapshotStore | None = None,
    ) -> T | None:
        """Load an aggregate, optionally using a snapshot for fast hydration.

        Implementation:
        1. If *snapshot_store* is provided:
           a. ``snapshot = await snapshot_store.get(self.aggregate_type, str(id_))``
           b. If found: create aggregate from snapshot state, set ``version``,
              then call
              ``event_store.read_stream(str(id_), from_version=snapshot.version)``
              and replay remaining events.
        2. If no snapshot was found (or no *snapshot_store*), fall back to
           full replay: ``event_store.read_stream(str(id_))`` from version 0.
        3. If ``StreamNotFoundError`` is raised and no snapshot was used,
           return ``None``.
        """
        ...
