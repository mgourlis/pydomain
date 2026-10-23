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

`check_and_set()` is part of the protocol: `IdempotencyBehavior` uses `get()` and `set()`, and `EventIdempotencyBehavior` uses `get()` and `set()` with a reserving store, `contains()` and `check_and_set()` without one, so a store must implement all four. Keep the marks of `check_and_set()` apart from the cached results that `get()` reads, as above: the Protocol and `ProcessedMessageStoreContract` require the split ([ADR-080](../../../adr/ADR-080-identify-delivered-events-for-event-idempotency.md)), `check_and_set()` creates no result and reads marks only, `contains()` covers both, and `ProcessedMessageStoreContract` checks it. The contract still checks the split; with event keys under their own namespace, no command result can match an event key. Event keys are derived from the `event_id` and the behavior's `consumer` under pydomain's own namespace, so the command and event behaviors can share one store ([Event Idempotency](../../concepts/cqrs/idempotency-and-locking.md#event-idempotency)).

For production, use Redis, PostgreSQL, or another persistent store.

To refuse concurrent duplicates as well, the store also implements `MessageReservationStore`: `reserve(command_id)` returns a token, or `None` when the id is already reserved, and `release(command_id, token)` frees only the reservation that token took. `IdempotencyBehavior` then reserves each command before its handler runs, and a concurrent duplicate gets `CommandInProgressError` instead of running. `FakeProcessedMessageStore` and the [Redis example](#production-store-redis-example) implement both Protocols; check your own adapter with [`MessageReservationStoreContract`](../testing/run-contract-suites.md).

The behavior calls `reserve`, `release` and the `set` after a commit in a task of its own under asyncio, protected from cancellation as the commit is, so a store must work from any task: build it on a connection pool (`redis.asyncio`, an asyncpg pool, an `AsyncEngine` with a short session per call), never on the unit of work's task-scoped session.

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

With a reserving store, a duplicate that arrives while the first dispatch still runs gets `CommandExecutionError` whose `__cause__` is `CommandInProgressError`; nothing of it runs or commits. Retry it later: it then gets the cached result, or runs the handler if the first dispatch failed. A command that fails or is cancelled before its commit releases its reservation. A reservation stays until the store's TTL frees it when its process crashed, when its `release` failed (a note `clean-up also failed: <type>` on the error), when the `set` after the commit failed, and when the dispatch was closed while suspended; until then, retries get `CommandInProgressError`.

> **Known limits:** without a reserving store, two concurrent dispatches of the same `command_id` can both run the handler, because idempotency runs before locking. Recording is at-least-once: a retry also runs the handler again after a crash between the commit and the record, after a failed `store.set()` (with a reserving store, once its TTL freed the id), and after an ambiguous commit failure (`_commit()` raised although the database committed, so the caller saw a failure for work that was persisted). Make handlers safe to repeat where that matters. Exactly-once recording is planned for a later release.

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
  → store.reserve("def-456") → token (with a reserving store)
  → next(): run handler, commit raises
  → store.release("def-456", token); nothing recorded; the error reaches the caller
  → a retry with def-456 runs the handler again

Concurrent duplicate, with a reserving store (command_id=ghi-789):
  first:     store.get → MISSING, store.reserve → token, next() runs ...
  duplicate: store.get → MISSING, store.reserve → None, store.get → MISSING
  → CommandInProgressError, before its handler; the first dispatch carries on
```

With a reserving store, the first dispatch's `store.set()` completes its reservation.

## Production Store (Redis Example)

<!-- docs-test: skip -->
```python
import importlib
import json
from collections.abc import Hashable
from typing import Any
from uuid import UUID, uuid4
import redis.asyncio as redis

# Delete the key only if it still holds this caller's token, in one step.
_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class RedisProcessedMessageStore:
    """A ProcessedMessageStore and MessageReservationStore on one Redis client."""

    def __init__(
        self, client: redis.Redis, ttl: int = 86400, reservation_ttl: int = 300
    ) -> None:
        self._client = client
        self._ttl = ttl
        self._reservation_ttl = reservation_ttl  # longer than a handler and its commit

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
        # Complete the reservation: overwrite the token with "done", only if the
        # key exists (XX), keeping its TTL (KEEPTTL, Redis 6 or later). A release
        # with the old token then frees nothing, and reserve keeps refusing.
        await self._client.set(f"reserved:{command_id}", "done", xx=True, keepttl=True)

    async def contains(self, command_id: UUID) -> bool:
        return bool(await self._client.exists(f"cmd:{command_id}", f"seen:{command_id}"))

    async def check_and_set(self, command_id: UUID) -> bool:
        # SET NX is atomic: it succeeds only for the first caller
        created = await self._client.set(f"seen:{command_id}", "1", nx=True, ex=self._ttl)
        return not created

    async def reserve(self, command_id: UUID) -> Hashable | None:
        # SET NX with a TTL: one caller gets the reservation, and a crashed
        # process's reservation expires. The value is this caller's token.
        token = uuid4().hex
        taken = await self._client.set(
            f"reserved:{command_id}", token, nx=True, ex=self._reservation_ttl
        )
        return token if taken else None

    async def release(self, command_id: UUID, token: Hashable) -> None:
        # Compare-and-delete: a token whose reservation expired, and was taken
        # by another dispatch since, frees nothing.
        await self._client.eval(_RELEASE, 1, f"reserved:{command_id}", str(token))
```

Results, the marks of `check_and_set` and reservations use separate keys (`cmd:`, `seen:`, `reserved:`). `set` completes the reservation by replacing its token with `done` (`SET … XX KEEPTTL`, which needs Redis 6 or later), so the key keeps refusing `reserve` until its TTL and a late `release` with the old token frees nothing: a duplicate that missed the result in its first `get` gets `None` from `reserve`, and the behavior's second `get` returns the result. Every method uses the shared client's connection pool, so the store works from any task.

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

To test a concurrent duplicate, gather two dispatches of one command; `FakeProcessedMessageStore` reserves the first, and the second is refused:

```python
import asyncio

from pydomain.cqrs import CommandInProgressError


class SlowPlaceOrderHandler(PlaceOrderHandler):
    async def __call__(self, command: PlaceOrder, uow: object) -> PlaceOrderResult:
        await asyncio.sleep(0.01)  # the duplicate arrives while this runs
        return await super().__call__(command, uow)


async def test_concurrent_duplicate_is_refused():
    slow = SlowPlaceOrderHandler()
    bus = CommandBus()
    bus.register(
        PlaceOrder, slow, uow_factory,
        behaviors=[IdempotencyBehavior(FakeProcessedMessageStore())],
    )
    cmd = PlaceOrder(customer_id="customer-3")

    first, duplicate = await asyncio.gather(
        bus.dispatch(cmd), bus.dispatch(cmd), return_exceptions=True
    )

    assert isinstance(duplicate, CommandExecutionError)
    assert isinstance(duplicate.__cause__, CommandInProgressError)
    assert slow.calls == 1
```

<!-- docs-test: check
await test_idempotency()
await test_failed_commit_is_not_cached()
await test_concurrent_duplicate_is_refused()
-->

## Non-Command Messages

The behavior checks `ctx.metadata.get("command_id")`. If absent (e.g., queries, events), it passes through to `next()` without consulting the store. This means the same behavior instance can sit on a shared pipeline without interfering with non-command messages.

## See Also

- [Idempotency & Locking concept](../../concepts/cqrs/idempotency-and-locking.md)
- [Add Distributed Locking](add-distributed-locking.md)
- [Add a Pipeline Behavior](add-pipeline-behavior.md)
- [Configure the Command Bus](configure-command-bus.md)
