# How to Use a Fake Lock Provider

<!-- docs-test: runnable -->

<!-- docs-test: setup
from uuid import UUID

from pydomain.cqrs import Command, CommandResult
from pydomain.infrastructure import bootstrap
from pydomain.testing import FakeUnitOfWork


class PlaceOrderResult(CommandResult):
    order_id: UUID


class PlaceOrder(Command[PlaceOrderResult]):
    order_id: UUID


class PlaceOrderHandler:
    def __init__(self, repo: object) -> None:
        self._repo = repo

    async def __call__(self, command: PlaceOrder, uow: object) -> PlaceOrderResult:
        return PlaceOrderResult(order_id=command.order_id)


repo = object()
uow_factory = FakeUnitOfWork
app = await bootstrap()
-->

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
from pydomain.cqrs import AggregateLockingBehavior
from pydomain.cqrs.locking import DictLockKeyResolver


lock_provider = FakeLockProvider()
resolver = DictLockKeyResolver()
resolver.register(PlaceOrder, lambda cmd: [f"order-{cmd.order_id}"])

behavior = AggregateLockingBehavior(lock_provider, resolver)

# Register on the application's command bus
app.message_bus.register_command(
    PlaceOrder,
    PlaceOrderHandler(repo),
    uow_factory,
    behaviors=[behavior],
)

await app.dispatch(PlaceOrder(order_id=UUID("00000000-0000-4000-8000-000000000001")))
await app.shutdown()
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

<!-- docs-test: check
await test_concurrent_commands_serialized()
-->

## Complete Example

```python
import pytest
import asyncio

from pydomain.cqrs import LockReentryError
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
        """A second acquire of a held key from the same task raises."""
        await locks.acquire("key-1")
        with pytest.raises(LockReentryError):
            await locks.acquire("key-1")
        # The key stays held by the first acquire.
        await locks.release("key-1")
```

<!-- docs-test: check
suite = TestFakeLockProvider()
for name in (
    "test_acquire_and_release",
    "test_release_unacquired_raises",
    "test_different_keys_independent",
    "test_same_key_serializes",
    "test_not_reentrant",
):
    await getattr(suite, name)(FakeLockProvider())
-->

## Important: Not Reentrant

Locks are **not reentrant** (ADR-076). When the task that holds a key calls `acquire()` for it again, `FakeLockProvider` raises `LockReentryError` instead of deadlocking, and the key stays held. The message names no key:

```text
FakeLockProvider: the current task already holds this lock key; locks are not reentrant (ADR-076)
```

The usual cause is a handler that dispatches a nested command with the same lock key. Through the bus, the nested dispatch's `LockReentryError` arrives wrapped in `CommandExecutionError`, as the cause of the nested command's own `CommandExecutionError`. A production provider may block instead of raising, so treat the error as a bug in the handler, not as something to catch.

"The same task" is the asyncio task. A nested acquire from a task the holder spawned (`asyncio.create_task`, a task group) is a different task, so the fake makes it wait, and it deadlocks if the holder waits for it.

## Important: Releases Are Ownership-Safe

Each `acquire()` takes a hold token and keeps it in the calling context (a `ContextVar`). `release(key)` frees the key only when the calling context's token belongs to the current hold: the task that acquired, or a task it started while it held the key. Any other release does nothing, so a late or repeated release never frees a lock another holder took (ADR-076, B-68). This is what makes the retry `AggregateLockingBehavior` makes after an interrupted release safe: its background task is started from the caller's context. A release from a task that was started before the acquire, or from an unrelated task, is a silent no-op, so in a test release the key from the task that holds it. Releasing a key the fake never saw still raises `KeyError`.

## Testing Release Failures

To check how your code behaves when the lock server fails on release, wrap the fake so `release()` raises. A failed release after a successful handler doesn't fail the command; `AggregateLockingBehavior` logs a WARNING on `pydomain.pipeline` naming the command type and the error type:

```python
import logging


class FailingRelease(FakeLockProvider):
    async def release(self, key: str) -> None:
        raise ConnectionError("synthetic release failure")


class Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


async def test_failed_release_does_not_fail_the_command():
    resolver = DictLockKeyResolver()
    resolver.register(PlaceOrder, lambda cmd: [f"order-{cmd.order_id}"])
    behavior = AggregateLockingBehavior(FailingRelease(), resolver)
    app = await bootstrap()
    app.message_bus.register_command(
        PlaceOrder, PlaceOrderHandler(repo), uow_factory, behaviors=[behavior]
    )
    order_id = UUID("00000000-0000-4000-8000-000000000002")

    collect = Collect()
    pipeline_log = logging.getLogger("pydomain.pipeline")
    pipeline_log.addHandler(collect)
    try:
        result = await app.dispatch(PlaceOrder(order_id=order_id))
    finally:
        pipeline_log.removeHandler(collect)
        await app.shutdown()

    assert result.order_id == order_id
    assert collect.messages == ["Lock release failed for PlaceOrder: ConnectionError"]
```

<!-- docs-test: check
await test_failed_release_does_not_fail_the_command()
-->

## Expected Outcome

Your tests use `FakeLockProvider` to verify distributed locking behavior. Locks serialize concurrent access to the same key, different keys are independent, and the fake integrates with `AggregateLockingBehavior` for pipeline testing.

## See Also

- [Idempotency & Locking concept](../../concepts/cqrs/idempotency-and-locking.md)
- [Add Distributed Locking](../cqrs/add-distributed-locking.md)
- [Testing philosophy](../../concepts/testing/testing-philosophy.md)
