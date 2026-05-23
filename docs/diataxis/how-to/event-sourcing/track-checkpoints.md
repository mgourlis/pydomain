# How to Track Checkpoints

> **Adoption Level:** 5 · Prerequisites: [Subscriptions concept](../../concepts/es/subscriptions.md), [Projections concept](../../concepts/es/projections.md)

This guide shows how to use a `CheckpointStore` to persist subscription progress so that event processing resumes from the right position after a restart.

## 1. Choose a checkpoint store

Use `FakeCheckpointStore` for tests:

```python
from pydomain.testing.fake_checkpoint_store import FakeCheckpointStore

checkpoint_store = FakeCheckpointStore()
```

For production, implement `CheckpointStore`:

```python
from pydomain.es.checkpoint_store import CheckpointStore

class PostgresCheckpointStore:
    def __init__(self, connection_pool) -> None:
        self._pool = connection_pool

    async def load(self, subscription_id: str) -> int:
        row = await self._pool.fetchrow(
            "SELECT checkpoint FROM subscription_checkpoints WHERE subscription_id = $1",
            subscription_id,
        )
        return row["checkpoint"] if row else 0

    async def save(self, subscription_id: str, checkpoint: int) -> None:
        await self._pool.execute(
            "INSERT INTO subscription_checkpoints (subscription_id, checkpoint) "
            "VALUES ($1, $2) ON CONFLICT (subscription_id) DO UPDATE SET checkpoint = $2",
            subscription_id,
            checkpoint,
        )
```

`load()` returns `0` for unknown subscriptions — meaning "start from the beginning of the global event log."

## 2. Load the checkpoint before processing

```python
checkpoint = await checkpoint_store.load("order-summary")
# Returns 0 on first run, then the last saved position
```

## 3. Read new events from the checkpoint position

```python
stream = await event_store.read_all(from_version=checkpoint)

for event in stream.events:
    await projection.apply(event)
```

`read_all(from_version=N)` returns events at global positions N, N+1, N+2, ... through the end of the log. When `checkpoint=0`, it returns the entire log.

## 4. Save the checkpoint after processing

Save after successfully processing — not before:

```python
for event in stream.events:
    await projection.apply(event)

await checkpoint_store.save("order-summary", projection.checkpoint)
```

Saving after processing ensures at-least-once semantics. If the process crashes mid-batch, the checkpoint stays at its previous value and the same events will be redelivered.

## 5. Wire into a catch-up loop

```python
import asyncio


async def catch_up_loop(
    projection: EventSourcedProjection,
    event_store: EventStore,
    checkpoint_store: CheckpointStore,
    subscription_id: str,
    interval: float = 1.0,
) -> None:
    while True:
        checkpoint = await checkpoint_store.load(subscription_id)
        stream = await event_store.read_all(from_version=checkpoint)

        for event in stream.events:
            await projection.apply(event)

        if stream.events:
            await checkpoint_store.save(subscription_id, projection.checkpoint)

        await asyncio.sleep(interval)
```

This is the pattern that `SubscriptionRunner` automates. For simple cases you can write the loop by hand; for production use, subclass `SubscriptionRunner` instead.

## 6. Make projections idempotent

At-least-once delivery means a projection may see the same event more than once. Design handlers to tolerate duplicates:

```python
class OrderSummaryProjection(EventSourcedProjection):
    def __init__(self) -> None:
        super().__init__()
        self._seen_event_ids: set[str] = set()

    async def _when_OrderPlaced(self, event: OrderPlaced) -> None:
        if event.event_id in self._seen_event_ids:
            return  # Already processed
        self._seen_event_ids.add(event.event_id)
        self.total_orders += 1
```

For projections backed by a datastore, use upserts or idempotency keys instead of in-memory sets.

## Expected outcome

A projection that tracks its position in the global event log and resumes from the last saved checkpoint after a restart. No events are missed, and duplicate delivery is handled through idempotent handlers.

## Next steps

- [Catch-Up Subscriptions Recipe](../../how-to/recipes/subscriptions-catchup.md) — full `SubscriptionRunner` pipeline
- [Create an ES Projection](create-es-projection.md) — building projections from event streams

## Cross-references

- **ADR-052**: Checkpoint store vs snapshot store
