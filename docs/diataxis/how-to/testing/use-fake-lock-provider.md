# How to Use a Fake Lock Provider

> **Prerequisites:** [Idempotency & Locking concept](../../concepts/cqrs/idempotency-and-locking.md), [Add Distributed Locking](../cqrs/add-distributed-locking.md), [Testing philosophy](../../concepts/testing/testing-philosophy.md)

## Problem

You need to test command handlers that use distributed locking (via the `AggregateLockingBehavior` pipeline behavior) without a real lock provider. Tests must verify that locks are acquired and released, and that concurrent access is serialized.

## Solution

Use `FakeLockProvider` from `pydomain.testing` — an in-memory implementation of the `LockProvider` protocol backed by `asyncio.Lock` per key.

## Steps

### 1. Import FakeLockProvider

```python
from pydomain.testing import FakeLockProvider
```

### 2. Create a fake lock provider

```python
locks = FakeLockProvider()
```

### 3. Acquire and release locks

```python
await locks.acquire("order-abc123")
# ... critical section ...
await locks.release("order-abc123")
```

Releasing a lock that was never acquired raises `KeyError`.

### 4. Use with AggregateLockingBehavior

```python
from pydomain.cqrs.locking import AggregateLockingBehavior, DictLockKeyResolver


lock_provider = FakeLockProvider()
resolver = DictLockKeyResolver({"PlaceOrder": lambda cmd: f"order-{cmd.order_id}"})

behavior = AggregateLockingBehavior(lock_provider, resolver)

# Register on command bus
app.message_bus.register_command(
    PlaceOrder,
    PlaceOrderHandler(repo),
    uow_factory,
    behaviors=[behavior],
)
```

The behavior acquires the lock before the handler runs and releases it after.

### 5. Test lock serialization

```python
import asyncio


async def test_concurrent_commands_serialized():
    locks = FakeLockProvider()

    order = []

    async def critical_section(task_id: int) -> None:
        await locks.acquire("order-1")
        try:
            order.append(task_id)
            await asyncio.sleep(0.01)  # simulate work
        finally:
            await locks.release("order-1")

    # Run two tasks concurrently
    await asyncio.gather(
        critical_section(1),
        critical_section(2),
    )

    # Tasks are serialized — order is deterministic
    assert order == [1, 2]
```

## Complete Example

```python
import pytest
import asyncio

from pydomain.testing import FakeLockProvider


class TestFakeLockProvider:
    @pytest.fixture
    def locks(self) -> FakeLockProvider:
        return FakeLockProvider()

    async def test_acquire_and_release(self, locks):
        await locks.acquire("key-1")
        # Lock held
        await locks.release("key-1")

    async def test_release_unacquired_raises(self, locks):
        with pytest.raises(KeyError):
            await locks.release("nonexistent")

    async def test_different_keys_independent(self, locks):
        await locks.acquire("key-a")
        # Different key — should not block
        await locks.acquire("key-b")
        await locks.release("key-b")
        await locks.release("key-a")

    async def test_same_key_serializes(self, locks):
        results = []

        async def worker(name: str) -> None:
            await locks.acquire("shared")
            try:
                results.append(f"{name}-enter")
                await asyncio.sleep(0.01)
                results.append(f"{name}-exit")
            finally:
                await locks.release("shared")

        await asyncio.gather(worker("a"), worker("b"))

        # Execution is serialized — a enters and exits before b
        assert results == ["a-enter", "a-exit", "b-enter", "b-exit"]

    async def test_not_reentrant(self, locks):
        """FakeLockProvider is NOT reentrant — acquiring twice from same task deadlocks."""
        await locks.acquire("key-1")
        # Do NOT call acquire("key-1") again from the same task without releasing
        await locks.release("key-1")
```

## Important: Not Reentrant

`FakeLockProvider` uses `asyncio.Lock` internally, which is **not reentrant**. Acquiring the same key twice from the same task without releasing will deadlock. This matches the behavior of most real distributed lock providers (Redis Redlock, etc.).

## Expected Outcome

Your tests use `FakeLockProvider` to verify distributed locking behavior. Locks serialize concurrent access to the same key, different keys are independent, and the fake integrates with `AggregateLockingBehavior` for pipeline testing.

## See Also

- [Idempotency & Locking concept](../../concepts/cqrs/idempotency-and-locking.md)
- [Add Distributed Locking](../cqrs/add-distributed-locking.md)
- [Testing philosophy](../../concepts/testing/testing-philosophy.md)
