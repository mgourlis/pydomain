# How to Add Idempotency

<!-- docs-test: runnable -->
<!-- docs-test: setup
from pydomain.cqrs import Command, CommandBus, CommandResult
from pydomain.testing import FakeUnitOfWork


class PlaceOrderResult(CommandResult):
    status: str


class PlaceOrder(Command[PlaceOrderResult]):
    customer_id: str


class PlaceOrderHandler:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, command: PlaceOrder, uow: object) -> PlaceOrderResult:
        self.calls += 1
        return PlaceOrderResult(status="placed")


handler = PlaceOrderHandler()
uow_factory = FakeUnitOfWork
-->

> **Prerequisites:** [Idempotency & Locking concept](../../concepts/cqrs/idempotency-and-locking.md), [Configure the Command Bus](configure-command-bus.md)

## Problem

You need to handle duplicate commands safely — at-least-once delivery from message brokers, client retries, or saga replays.

## Solution

Add `IdempotencyBehavior` to the command pipeline, backed by a `ProcessedMessageStore` implementation.

## Steps

### 1. Choose or implement a store

```python
from typing import Any
from uuid import UUID
from pydomain.cqrs.idempotency import ProcessedMessageStore, MISSING


class InMemoryProcessedMessageStore:
    def __init__(self) -> None:
        self._results: dict[UUID, Any] = {}
        self._seen: set[UUID] = set()

    async def get(self, command_id: UUID) -> Any:
        return self._results.get(command_id, MISSING)

    async def set(self, command_id: UUID, result: Any) -> None:
        self._results[command_id] = result

    async def contains(self, command_id: UUID) -> bool:
        return command_id in self._results or command_id in self._seen

    async def check_and_set(self, command_id: UUID) -> bool:
        if command_id in self._seen:
            return True
        self._seen.add(command_id)
        return False
```

`check_and_set()` is part of the protocol: `EventIdempotencyBehavior` calls it, and `IdempotencyBehavior` uses `get()` and `set()`, so a store must implement all four. Keep the marks of `check_and_set()` apart from the cached results that `get()` reads, as above, and give each behavior its own store (or key space): a store shared between `IdempotencyBehavior` and `EventIdempotencyBehavior` makes the event handlers skip events they should handle (a known limit, planned for a later release).

For production, use Redis, PostgreSQL, or another persistent store.

### 2. Create the behavior

```python
from pydomain.cqrs.behaviors import IdempotencyBehavior

store = InMemoryProcessedMessageStore()
idempotency = IdempotencyBehavior(store)
```

### 3. Add to the pipeline

<!-- docs-test: skip -->
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

The behavior stores the result after the unit of work has committed: the commit runs inside `next()`. A command whose commit fails is therefore not cached, and a retry with the same `command_id` runs the handler again. A `store.set()` that fails after the commit is logged as a WARNING on `pydomain.pipeline`, naming the error's type only, and the result is still returned.

> **Known limits:** with idempotency before locking, two concurrent dispatches of the same `command_id` can both run the handler. Recording is at-least-once: a retry also runs the handler again after a crash between the commit and the record, after a failed `store.set()`, and after an ambiguous commit failure (`_commit()` raised although the database committed, so the caller saw a failure for work that was persisted). Make handlers safe to repeat where that matters. Both are known limits, planned for a later release.

## How It Works

```
First dispatch (command_id=abc-123):
  IdempotencyBehavior: store.get("abc-123") → MISSING
  → next(): run handler, commit the unit of work
  → store.set("abc-123", result)
  → Return result

Duplicate dispatch (command_id=abc-123):
  IdempotencyBehavior: store.get("abc-123") → cached result
  → Return cached result immediately
  → Handler is never called, nothing is committed

Dispatch whose commit fails (command_id=def-456):
  IdempotencyBehavior: store.get("def-456") → MISSING
  → next(): run handler, commit raises
  → nothing recorded; the error reaches the caller
  → a retry with def-456 runs the handler again
```

## Production Store (Redis Example)

<!-- docs-test: skip -->
```python
import importlib
import json
from typing import Any
from uuid import UUID
import redis.asyncio as redis


class RedisProcessedMessageStore:
    def __init__(self, client: redis.Redis, ttl: int = 86400) -> None:
        self._client = client
        self._ttl = ttl

    async def get(self, command_id: UUID) -> Any:
        data = await self._client.get(f"cmd:{command_id}")
        if data is None:
            return MISSING
        stored = json.loads(data)
        module, _, name = stored["type"].partition(":")
        result_type = getattr(importlib.import_module(module), name)
        return result_type.model_validate(stored["data"])  # a CommandResult, as the first call returned

    async def set(self, command_id: UUID, result: Any) -> None:
        # Command results are Pydantic models: keep the type with the data so a
        # duplicate gets the same kind of object back, not a string.
        stored = {
            "type": f"{type(result).__module__}:{type(result).__qualname__}",
            "data": result.model_dump(mode="json"),
        }
        await self._client.setex(f"cmd:{command_id}", self._ttl, json.dumps(stored))

    async def contains(self, command_id: UUID) -> bool:
        return bool(await self._client.exists(f"cmd:{command_id}", f"seen:{command_id}"))

    async def check_and_set(self, command_id: UUID) -> bool:
        # SET NX is atomic: it succeeds only for the first caller
        created = await self._client.set(f"seen:{command_id}", "1", nx=True, ex=self._ttl)
        return not created
```

The store keeps the result's type with its data. `json.dumps(result, default=str)` would store the text of the model, and a duplicate dispatch would return that string instead of the result. Set a TTL to prevent unbounded growth. Results older than the TTL are evicted — choose a TTL longer than your maximum retry window.

## Testing

Use `FakeProcessedMessageStore` in tests:

```python
from pydomain.testing.fake_processed_message_store import (
    FakeProcessedMessageStore,
)


async def test_idempotency():
    store = FakeProcessedMessageStore()
    bus = CommandBus()
    bus.register(
        PlaceOrder, handler, uow_factory,
        behaviors=[IdempotencyBehavior(store)],
    )

    cmd = PlaceOrder(customer_id="customer-1")

    # First dispatch — handler runs
    result1, _ = await bus.dispatch(cmd)
    assert await store.contains(cmd.command_id)

    # Duplicate — handler skipped, same result returned
    result2, _ = await bus.dispatch(cmd)
    assert result1 == result2
    assert handler.calls == 1
```

To test the retry after a failed commit, make the fake unit of work fail once with `fail_at`:

```python
from pydomain.cqrs import CommandExecutionError


async def test_failed_commit_is_not_cached():
    store = FakeProcessedMessageStore()
    counting = PlaceOrderHandler()
    units = [FakeUnitOfWork(fail_at="commit"), FakeUnitOfWork()]
    bus = CommandBus()
    bus.register(
        PlaceOrder, counting, lambda: units.pop(0),
        behaviors=[IdempotencyBehavior(store)],
    )
    cmd = PlaceOrder(customer_id="customer-2")

    try:
        await bus.dispatch(cmd)  # the commit fails: nothing is recorded
    except CommandExecutionError:
        pass
    assert not await store.contains(cmd.command_id)

    await bus.dispatch(cmd)  # the retry runs the handler again
    assert counting.calls == 2
```

<!-- docs-test: check
await test_idempotency()
await test_failed_commit_is_not_cached()
-->

## Non-Command Messages

The behavior checks `ctx.metadata.get("command_id")`. If absent (e.g., queries, events), it passes through to `next()` without consulting the store. This means the same behavior instance can sit on a shared pipeline without interfering with non-command messages.

## See Also

- [Idempotency & Locking concept](../../concepts/cqrs/idempotency-and-locking.md)
- [Add Distributed Locking](add-distributed-locking.md)
- [Add a Pipeline Behavior](add-pipeline-behavior.md)
- [Configure the Command Bus](configure-command-bus.md)
