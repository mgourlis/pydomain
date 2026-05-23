# How to Use a Fake Processed Command Store

> **Prerequisites:** [Idempotency & Locking concept](../../concepts/cqrs/idempotency-and-locking.md), [Add Idempotency](../cqrs/add-idempotency.md), [Testing philosophy](../../concepts/testing/testing-philosophy.md)

## Problem

You need to test command idempotency — ensuring that replaying a command with the same `command_id` returns the cached result instead of executing the handler again. You need an in-memory store for tests.

## Solution

Use `FakeProcessedCommandStore` from `pydomain.testing` — an in-memory implementation of the `ProcessedCommandStore` protocol that returns the `MISSING` sentinel for unknown command IDs.

## Steps

### 1. Import FakeProcessedCommandStore

```python
from pydomain.testing import FakeProcessedCommandStore
```

### 2. Create a fake processed command store

```python
store = FakeProcessedCommandStore()
```

### 3. Check for unknown commands

```python
from uuid import uuid4

cmd_id = uuid4()
result = await store.get(cmd_id)
# Returns MISSING sentinel (from pydomain.cqrs.idempotency)
```

### 4. Store a processed command result

```python
await store.set(cmd_id, {"order_id": "abc-123"})
```

### 5. Retrieve a cached result

```python
result = await store.get(cmd_id)
print(result)  # {"order_id": "abc-123"}
```

### 6. Check existence

```python
exists = await store.contains(cmd_id)
print(exists)  # True
```

### 7. Use with IdempotencyBehavior

```python
from pydomain.cqrs.idempotency import IdempotencyBehavior


store = FakeProcessedCommandStore()
behavior = IdempotencyBehavior(store)

# Register on command bus
app.message_bus.register_command(
    PlaceOrder,
    PlaceOrderHandler(repo),
    uow_factory,
    behaviors=[behavior],
)
```

When a command is dispatched:
- `behavior` calls `store.get(command.command_id)`
- If `MISSING`: handler runs, result is stored via `store.set(command.command_id, result)`
- If cached: handler is skipped, cached result returned, `IdempotentCommandIgnored` raised

## Complete Example

```python
import pytest
from uuid import uuid4

from pydomain.testing import FakeProcessedCommandStore
from pydomain.cqrs.idempotency import MISSING


class TestFakeProcessedCommandStore:
    @pytest.fixture
    def store(self) -> FakeProcessedCommandStore:
        return FakeProcessedCommandStore()

    async def test_unknown_command_returns_missing(self, store):
        result = await store.get(uuid4())
        assert result is MISSING

    async def test_store_and_retrieve_result(self, store):
        cmd_id = uuid4()
        await store.set(cmd_id, {"status": "ok", "order_id": "abc"})

        result = await store.get(cmd_id)
        assert result == {"status": "ok", "order_id": "abc"}

    async def test_contains(self, store):
        cmd_id = uuid4()
        assert not await store.contains(cmd_id)

        await store.set(cmd_id, "result")
        assert await store.contains(cmd_id)

    async def test_overwrite(self, store):
        cmd_id = uuid4()
        await store.set(cmd_id, "first")
        await store.set(cmd_id, "second")

        result = await store.get(cmd_id)
        assert result == "second"

    async def test_independent_command_ids(self, store):
        id1 = uuid4()
        id2 = uuid4()

        await store.set(id1, "result-1")
        await store.set(id2, "result-2")

        assert await store.get(id1) == "result-1"
        assert await store.get(id2) == "result-2"

    async def test_idempotency_behavior_integration(self, store):
        """Simulate what IdempotencyBehavior does."""
        cmd_id = uuid4()

        # First dispatch
        result = await store.get(cmd_id)
        assert result is MISSING  # not processed yet
        # Handler would run here...
        await store.set(cmd_id, PlaceOrderResult(order_id="abc-123"))

        # Second dispatch (duplicate)
        result = await store.get(cmd_id)
        assert result is not MISSING  # already processed
        # Handler is skipped, cached result returned
        assert result.order_id == "abc-123"
```

## Expected Outcome

Your tests use `FakeProcessedCommandStore` to verify command idempotency. Unknown commands return `MISSING`, cached results are retrieved, and duplicate commands are detected. The store integrates with `IdempotencyBehavior` for full pipeline testing.

## See Also

- [Idempotency & Locking concept](../../concepts/cqrs/idempotency-and-locking.md)
- [Add Idempotency](../cqrs/add-idempotency.md)
- [Testing philosophy](../../concepts/testing/testing-philosophy.md)
