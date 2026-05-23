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

The behavior acquires keys in sorted order and releases in reverse:

```python
keys = sorted(["account:def", "account:abc"])  # → ["account:abc", "account:def"]

# Acquire in order
for key in keys:
    await provider.acquire(key)

# Release in reverse in finally block
for key in reversed(keys):
    await provider.release(key)
```

If any acquire fails, already-held keys are released before raising.

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

Use a TTL to auto-release stale locks if a process crashes.

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
