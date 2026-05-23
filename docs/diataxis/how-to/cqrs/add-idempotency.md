# How to Add Idempotency

> **Prerequisites:** [Idempotency & Locking concept](../../concepts/cqrs/idempotency-and-locking.md), [Configure the Command Bus](configure-command-bus.md)

## Problem

You need to handle duplicate commands safely — at-least-once delivery from message brokers, client retries, or saga replays.

## Solution

Add `IdempotencyBehavior` to the command pipeline, backed by a `ProcessedCommandStore` implementation.

## Steps

### 1. Choose or implement a store

```python
from uuid import UUID
from pydomain.cqrs.idempotency import ProcessedCommandStore, MISSING


class InMemoryProcessedCommandStore:
    def __init__(self) -> None:
        self._results: dict[UUID, Any] = {}

    async def get(self, command_id: UUID) -> Any:
        return self._results.get(command_id, MISSING)

    async def set(self, command_id: UUID, result: Any) -> None:
        self._results[command_id] = result

    async def contains(self, command_id: UUID) -> bool:
        return command_id in self._results
```

For production, use Redis, PostgreSQL, or another persistent store.

### 2. Create the behavior

```python
from pydomain.cqrs.behaviors import IdempotencyBehavior

store = InMemoryProcessedCommandStore()
idempotency = IdempotencyBehavior(store)
```

### 3. Add to the pipeline

```python
bus.register(
    command_type=PlaceOrder,
    handler=PlaceOrderHandler(pricing_service),
    uow_factory=create_order_uow,
    behaviors=[
        LoggingBehavior(),           # Slot 1
        ValidationBehavior(),         # Slot 2
        IdempotencyBehavior(store),   # Slot 3 — before locking
        AggregateLockingBehavior(...),# Slot 4
    ],
)
```

The ordering matters: idempotency (slot 3) must come before locking (slot 4) so duplicate commands skip lock acquisition.

## How It Works

```
First dispatch (command_id=abc-123):
  IdempotencyBehavior: store.get("abc-123") → MISSING
  → Run handler
  → store.set("abc-123", result)
  → Return result

Duplicate dispatch (command_id=abc-123):
  IdempotencyBehavior: store.get("abc-123") → cached result
  → Return cached result immediately
  → Handler is never called
```

## Production Store (Redis Example)

```python
import json
from uuid import UUID
import redis.asyncio as redis


class RedisProcessedCommandStore:
    def __init__(self, client: redis.Redis, ttl: int = 86400) -> None:
        self._client = client
        self._ttl = ttl

    async def get(self, command_id: UUID) -> Any:
        data = await self._client.get(f"cmd:{command_id}")
        if data is None:
            return MISSING
        return json.loads(data)

    async def set(self, command_id: UUID, result: Any) -> None:
        await self._client.setex(
            f"cmd:{command_id}",
            self._ttl,
            json.dumps(result, default=str),
        )

    async def contains(self, command_id: UUID) -> bool:
        return await self._client.exists(f"cmd:{command_id}")
```

Set a TTL to prevent unbounded growth. Results older than the TTL are evicted — choose a TTL longer than your maximum retry window.

## Testing

Use `FakeProcessedCommandStore` in tests:

```python
from pydomain.testing.fake_processed_command_store import (
    FakeProcessedCommandStore,
)


async def test_idempotency():
    store = FakeProcessedCommandStore()
    bus = CommandBus()
    bus.register(
        PlaceOrder, handler, uow_factory,
        behaviors=[IdempotencyBehavior(store)],
    )

    cmd = PlaceOrder(customer_id=..., items=[...])

    # First dispatch — handler runs
    result1, _ = await bus.dispatch(cmd)
    assert store.contains(cmd.command_id)

    # Duplicate — handler skipped, same result returned
    result2, _ = await bus.dispatch(cmd)
    assert result1 == result2
```

## Non-Command Messages

The behavior checks `ctx.metadata.get("command_id")`. If absent (e.g., queries, events), it passes through to `next()` without consulting the store. This means the same behavior instance can sit on a shared pipeline without interfering with non-command messages.

## See Also

- [Idempotency & Locking concept](../../concepts/cqrs/idempotency-and-locking.md)
- [Add Distributed Locking](add-distributed-locking.md)
- [Add a Pipeline Behavior](add-pipeline-behavior.md)
- [Configure the Command Bus](configure-command-bus.md)
