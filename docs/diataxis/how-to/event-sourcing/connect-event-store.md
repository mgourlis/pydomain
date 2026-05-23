# How to Connect an Event Store

> **Adoption Level:** 4 · Prerequisites: [Event Store concept](../../concepts/es/event-store.md), [Event-Sourced Repositories concept](../../concepts/es/event-sourced-repositories.md)

This guide shows you how to wire an event store backend and use it with the repository.

## 1. Choose an event store implementation

For testing, use the built-in fake:

```python
from pydomain.testing.fake_event_store import FakeEventStore

event_store = FakeEventStore()
```

For production, implement the `EventStore` Protocol. Here's a sketch for PostgreSQL:

```python
from pydomain.es.event_store import EventStore
from pydomain.es.event_stream import EventStream


class PostgresEventStore(EventStore):
    def __init__(self, connection: AsyncConnection) -> None:
        self._conn = connection

    async def append_to_stream(
        self, aggregate_id: str, events, expected_version: int, command_id=None
    ) -> None:
        # BEGIN;
        # SELECT COUNT(*) FROM events WHERE aggregate_id = $1 FOR UPDATE;
        # If count != expected_version: raise ConcurrencyError
        # If command_id already exists: raise DuplicateCommandError
        # INSERT INTO events (aggregate_id, version, data, command_id) ...
        # COMMIT;

    async def read_stream(self, aggregate_id: str, from_version: int = 0) -> EventStream:
        # SELECT data, version FROM events WHERE aggregate_id = $1 AND version > $2 ORDER BY version;
        ...

    async def read_all(self, from_version: int = 0) -> EventStream:
        # SELECT data, version FROM events WHERE version > $1 ORDER BY version;
        ...
```

## 2. Create the repository

Pass the event store to `EventSourcedRepository`:

```python
from pydomain.es.event_sourced_repository import EventSourcedRepository


repository = EventSourcedRepository[Order, UUID](
    event_store=event_store,
    aggregate_cls=Order,
)
```

## 3. Optionally add snapshot support

If you want snapshots for faster aggregate loading:

```python
from pydomain.testing.fake_snapshot_store import FakeSnapshotStore
from pydomain.es.snapshot import SnapshotThresholdPolicy, RejectStaleSnapshotPolicy


repository = EventSourcedRepository[Order, UUID](
    event_store=event_store,
    aggregate_cls=Order,
    snapshot_store=FakeSnapshotStore(),                # For production: PostgresSnapshotStore(...)
    snapshot_policy=SnapshotThresholdPolicy(threshold=10),  # Snapshot every 10 events
    snapshot_schema_policy=RejectStaleSnapshotPolicy(),     # Reject stale snapshots
)
```

Snapshot configuration is optional — you can start without snapshots and add them later when replay performance becomes an issue.

## 4. Use the repository in a command handler

```python
class PlaceOrderHandler:
    def __init__(self, repository: EventSourcedRepository[Order, UUID]) -> None:
        self._repository = repository

    async def handle(self, cmd: PlaceOrder) -> PlaceOrderResult:
        order = await self._repository.get_by_id(cmd.order_id)
        if order is None:
            raise OrderNotFoundError(cmd.order_id)

        order.place()
        await self._repository.save(order, command_id=cmd.command_id)

        return PlaceOrderResult(order_id=order.id, status=order.status)
```

## Expected outcome

An event store wired to the repository, ready to persist and reconstruct event-sourced aggregates. If you added snapshots, aggregates with long event streams will load faster by starting from the snapshot and replaying only the tail events.

## Next steps

- [Implement an ES Repository](implement-es-repository.md) — custom repository patterns
- [Create an ES Projection](create-es-projection.md) — build read models from the event stream
