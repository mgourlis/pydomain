# How to Add Distributed Locking

> **Prerequisites:** [Idempotency & Locking concept](../../concepts/cqrs/idempotency-and-locking.md), [Configure the Command Bus](configure-command-bus.md)

## Problem

You need to prevent concurrent modification of the same aggregate from multiple command handlers running in parallel.

## Solution

Add `AggregateLockingBehavior` to the command pipeline, backed by a `LockProvider` and `LockKeyResolver`.

## Steps

### 1. Choose or implement a lock provider

```python
from pydomain.cqrs.locking import LockProvider


class InMemoryLockProvider:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    async def acquire(self, key: str) -> None:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        await self._locks[key].acquire()

    async def release(self, key: str) -> None:
        if key in self._locks:
            self._locks[key].release()
```

`asyncio.Lock` may be released from any task, which this provider relies on: the behavior releases from its own task.

For production, use Redis (`SETNX` with TTL) or a database advisory lock.

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

- **`release()` from another task.** The releases run in one background task that a cancellation cannot interrupt, so `release()` is not called from the task that called `acquire()`. Don't tie a lock to the acquiring task (an `anyio.Lock`, or a check against `asyncio.current_task()`); tie it to the key, or to a token you store per key.
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

```python
import redis.asyncio as redis


class RedisLockProvider:
    def __init__(self, client: redis.Redis, ttl: int = 30) -> None:
        self._client = client
        self._ttl = ttl

    async def acquire(self, key: str) -> None:
        acquired = False
        while not acquired:
            acquired = await self._client.set(
                f"lock:{key}", "1", nx=True, ex=self._ttl
            )
            if not acquired:
                await asyncio.sleep(0.1)

    async def release(self, key: str) -> None:
        await self._client.delete(f"lock:{key}")
```

Use a TTL to auto-release stale locks if a process crashes, or when a `release()` fails. The lock belongs to the key, not to a task, so the behavior's background release task can delete it.

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

    # Simulate concurrent dispatch — second call blocks
    ...
```

## See Also

- [Idempotency & Locking concept](../../concepts/cqrs/idempotency-and-locking.md)
- [Add Idempotency](add-idempotency.md)
- [Add a Pipeline Behavior](add-pipeline-behavior.md)
- [Configure the Command Bus](configure-command-bus.md)
