# How to Add Distributed Locking

<!-- docs-test: runnable -->

<!-- docs-test: setup
from uuid import UUID

from pydomain.cqrs import CommandBus, Command, CommandResult
from pydomain.cqrs.behaviors import (
    IdempotencyBehavior,
    LoggingBehavior,
    ValidationBehavior,
)
from pydomain.testing import FakeProcessedMessageStore, FakeUnitOfWork


class R(CommandResult):
    ok: bool = True


class PlaceOrder(Command[R]):
    order_id: UUID


class CancelOrder(Command[R]):
    order_id: UUID


class AddItem(Command[R]):
    order_id: UUID


class UpdateProfile(Command[R]):
    user_id: UUID


class TransferFunds(Command[R]):
    from_id: UUID
    to_id: UUID


class HealthCheck(Command[R]):
    pass


class PlaceOrderHandler:
    def __init__(self, pricing_service: object) -> None:
        self._pricing = pricing_service

    async def __call__(self, command: PlaceOrder, uow: object) -> R:
        return R()


pricing_service = None
handler = PlaceOrderHandler(pricing_service)
create_order_uow = uow_factory = FakeUnitOfWork
store = FakeProcessedMessageStore()
bus = CommandBus()
-->

> **Prerequisites:** [Idempotency & Locking concept](../../concepts/cqrs/idempotency-and-locking.md), [Configure the Command Bus](configure-command-bus.md)

## Problem

You need to prevent concurrent modification of the same aggregate from multiple command handlers running in parallel.

## Solution

Add `AggregateLockingBehavior` to the command pipeline, backed by a `LockProvider` and `LockKeyResolver`.

## Steps

### 1. Choose or implement a lock provider

```python
import asyncio
from contextvars import ContextVar

from pydomain.cqrs.locking import LockProvider

# The hold tokens the current context took, by key.
_tokens: ContextVar[dict[str, object]] = ContextVar("lock_tokens", default={})


class InMemoryLockProvider:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._owners: dict[str, object] = {}

    async def acquire(self, key: str) -> None:
        lock = self._locks.setdefault(key, asyncio.Lock())
        await lock.acquire()
        token = object()  # one token per acquire
        self._owners[key] = token
        _tokens.set({**_tokens.get(), key: token})

    async def release(self, key: str) -> None:
        token = _tokens.get().get(key)
        if token is None or self._owners.get(key) is not token:
            return  # not this context's hold: never free another holder's lock
        del self._owners[key]
        self._locks[key].release()


provider = InMemoryLockProvider()
```

`release(key)` takes no token, so the provider keeps one per acquire in a `ContextVar`. A task started from the acquiring context sees it, which is how the behavior's background release task, after a cancellation, can still release. A release whose token is not the current hold's does nothing, which makes `release()` ownership-safe (see [What Happens on Release](#what-happens-on-release)).

For production, use Redis (a token with a compare-and-delete, below) or a database advisory lock.

### 2. Configure the key resolver

```python
from pydomain.cqrs.locking import DictLockKeyResolver

resolver = DictLockKeyResolver()

# Map commands to lock keys — usually by aggregate ID
resolver.register(PlaceOrder, lambda cmd: [f"order:{cmd.order_id}"])
resolver.register(CancelOrder, lambda cmd: [f"order:{cmd.order_id}"])
resolver.register(AddItem, lambda cmd: [f"order:{cmd.order_id}"])
resolver.register(UpdateProfile, lambda cmd: [f"user:{cmd.user_id}"])
```

Each function receives the command and returns a list of lock keys.

### 3. Add to the pipeline

```python
from pydomain.cqrs.behaviors import AggregateLockingBehavior

bus.register(
    command_type=PlaceOrder,
    handler=PlaceOrderHandler(pricing_service),
    uow_factory=create_order_uow,
    behaviors=[
        LoggingBehavior(),
        ValidationBehavior(),
        IdempotencyBehavior(store),                # Slot 3
        AggregateLockingBehavior(provider, resolver), # Slot 4
    ],
)
```

## Multi-Aggregate Locking

When a command touches multiple aggregates, return multiple keys:

```python
resolver.register(
    TransferFunds,
    lambda cmd: sorted([f"account:{cmd.from_id}", f"account:{cmd.to_id}"]),
)
```

Keys are acquired in **sorted** order to prevent deadlocks across concurrent commands. The behavior handles sorting — but pre-sorting in the resolver ensures deterministic ordering.

## Deadlock Prevention

The behavior acquires keys in sorted order (ADR-019), so `["account:def", "account:abc"]` is always acquired as `account:abc`, then `account:def`.

## What Happens on Release

The behavior releases every key it acquired, in reverse order, however the command ends: after the handler returns, after the handler or an `acquire()` raises, and after a cancellation, even one that arrives while it waits on its second key (ADR-076). Your provider needs to support two things:

- **An ownership-safe `release()`, also from a background task.** Releases normally run in the task that called `acquire()`. If that task is cancelled while a release runs, the interrupted key and the rest move to a background task started from the caller's context, and that task calls the interrupted release **again**. The first call may already have freed the key and another command may hold it now, so a `release()` that deletes whatever lock the key has would free that command's lock and let a third one in. Tie each hold to its acquire with a token and release only while the token still owns the key, as both examples on this page do; being idempotent is not enough. A task-owned provider whose `release()` awaits (an `anyio.Lock` behind a network call, or a check against `asyncio.current_task()`) refuses the background releases; give it a lease or TTL for that case.
- **A failed `release()` is not raised to the caller** when the handler succeeded. The command commits, and the behavior logs a WARNING on `pydomain.pipeline` with the error's type, for example `Lock release failed for PlaceOrder: ConnectionError`. Use a TTL or lease so a lock whose release failed still frees itself. When the handler failed, the handler's error propagates with a note `clean-up also failed: ConnectionError`.

## Don't Nest Dispatches on the Same Key

Locks are not reentrant. A handler that dispatches another command whose keys include one it holds waits on its own lock. With `FakeLockProvider` that nested dispatch raises `LockReentryError` in your tests; with a production provider it hangs. A nested dispatch in a new task (`asyncio.create_task`) hangs with every provider. Dispatch the second command after the first one returns.

## No Locks for Queries

Queries don't need locking — they're read-only. If a query targets an aggregate that's being modified, the read store will reflect the last committed state. Use database isolation levels for read consistency, not distributed locks.

## Empty Key List = No Locking

If the resolver returns an empty list, the behavior passes through to `next()` without acquiring any locks:

```python
resolver.register(HealthCheck, lambda _: [])  # No lock needed
```

## Production Lock Provider (Redis Example)

<!-- docs-test: skip -->
```python
import asyncio
import uuid
from contextvars import ContextVar

import redis.asyncio as redis

# Delete the lock only while it still holds this acquire's token.
_RELEASE = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""

_tokens: ContextVar[dict[str, str]] = ContextVar("redis_lock_tokens", default={})


class RedisLockProvider:
    def __init__(self, client: redis.Redis, ttl: int = 30) -> None:
        self._client = client
        self._ttl = ttl
        self._release = client.register_script(_RELEASE)

    async def acquire(self, key: str) -> None:
        token = uuid.uuid4().hex  # one token per acquire
        while not await self._client.set(
            f"lock:{key}", token, nx=True, ex=self._ttl
        ):
            await asyncio.sleep(0.1)
        _tokens.set({**_tokens.get(), key: token})

    async def release(self, key: str) -> None:
        token = _tokens.get().get(key)
        if token is not None:
            await self._release(keys=[f"lock:{key}"], args=[token])
```

The value stored under the key is a token made for this acquire, and the Lua script deletes the key only while it still holds that token, in one atomic step on the server. A repeated or late release, such as the behavior's retry after an interrupted release, then finds another holder's token and deletes nothing. The token is kept in a `ContextVar` because `release(key)` takes no token argument; the behavior's background release task is started from the caller's context, so it sees it. Use a TTL to auto-release stale locks if a process crashes, or when a `release()` fails.

## Testing

Use a fake lock provider in tests:

```python
from pydomain.testing.fake_lock_provider import FakeLockProvider


async def test_locking_prevents_concurrent_access():
    provider = FakeLockProvider()
    resolver = DictLockKeyResolver()
    resolver.register(PlaceOrder, lambda cmd: [f"order:{cmd.order_id}"])

    bus = CommandBus()
    bus.register(
        PlaceOrder, handler, uow_factory,
        behaviors=[AggregateLockingBehavior(provider, resolver)],
    )

    result, _ = await bus.dispatch(
        PlaceOrder(order_id=UUID("00000000-0000-4000-8000-000000000001"))
    )
    assert result.ok
```

<!-- docs-test: check
await test_locking_prevents_concurrent_access()
-->

## See Also

- [Idempotency & Locking concept](../../concepts/cqrs/idempotency-and-locking.md)
- [Add Idempotency](add-idempotency.md)
- [Add a Pipeline Behavior](add-pipeline-behavior.md)
- [Configure the Command Bus](configure-command-bus.md)
