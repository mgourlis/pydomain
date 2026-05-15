from __future__ import annotations

from typing import Any

from pydomain.es.aggregate import EventSourcedAggregateRoot
from pydomain.es.event_sourced_repository import EventSourcedRepository
from pydomain.es.event_store import EventStore
from pydomain.es.exceptions import StreamNotFoundError
from pydomain.es.snapshot import SnapshotPolicy, SnapshotStore


class FakeEventSourcedRepository[T: EventSourcedAggregateRoot[Any], TId](
    EventSourcedRepository[T, TId]
):
    """In-memory fake for ``EventSourcedRepository``.

    Uses any ``EventStore`` implementation (typically ``FakeEventStore``
    in tests) and an aggregate class reference to implement save/load
    via event replay.

    Supports optional snapshot-first hydration when a ``SnapshotStore``
    is provided (either at construction time or per-call).
    """

    def __init__(
        self,
        event_store: EventStore,
        aggregate_cls: type[T],
        snapshot_store: SnapshotStore | None = None,
        snapshot_policy: SnapshotPolicy | None = None,
    ) -> None:
        self._event_store = event_store
        self._aggregate_cls = aggregate_cls
        self._snapshot_store = snapshot_store
        self._snapshot_policy = snapshot_policy

    @property
    def aggregate_type(self) -> str:
        return self._aggregate_cls.__name__

    async def save(
        self,
        aggregate: T,
        snapshot_store: SnapshotStore | None = None,
        snapshot_policy: SnapshotPolicy | None = None,
    ) -> None:
        events = aggregate.pull_events()
        if not events:
            return
        expected_version = aggregate.version - len(events)
        await self._event_store.append_to_stream(
            str(aggregate.id), events, expected_version
        )

        # Snapshot after successful append
        store = snapshot_store or self._snapshot_store
        policy = snapshot_policy or self._snapshot_policy
        if store is not None and policy is not None:
            if policy.should_snapshot(
                aggregate_type=self.aggregate_type,
                aggregate_id=str(aggregate.id),
                current_version=aggregate.version,
                pending_event_count=len(events),
            ):
                snapshot = aggregate._take_snapshot()
                await store.save(self.aggregate_type, snapshot)

    async def get_by_id(
        self,
        id_: TId,
        snapshot_store: SnapshotStore | None = None,
    ) -> T | None:
        aggregate_id = str(id_)
        store = snapshot_store or self._snapshot_store

        # Snapshot-first hydration: rebuild from snapshot then replay tail
        from_version = 0
        if store is not None:
            snapshot = await store.get(self.aggregate_type, aggregate_id)
            if snapshot is not None:
                aggregate = self._aggregate_cls(id=id_)
                for field, value in snapshot.state.items():
                    if field != "id":
                        setattr(aggregate, field, value)
                aggregate.version = snapshot.version
                from_version = snapshot.version
                try:
                    stream = await self._event_store.read_stream(
                        aggregate_id, from_version
                    )
                except StreamNotFoundError:
                    return aggregate
                for event in stream.events:
                    aggregate._replay(event)
                return aggregate

        # Fallback to full replay from scratch
        try:
            stream = await self._event_store.read_stream(aggregate_id, from_version)
        except StreamNotFoundError:
            return None
        aggregate = self._aggregate_cls(id=id_)
        for event in stream.events:
            aggregate._replay(event)
        return aggregate
