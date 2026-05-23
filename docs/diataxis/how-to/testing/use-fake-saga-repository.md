# How to Use a Fake Saga Repository

> **Prerequisites:** [Saga concept](../../concepts/sagas/saga.md), [Saga Repository concept](../../concepts/sagas/saga-repository.md), [Testing philosophy](../../concepts/testing/testing-philosophy.md)

## Problem

You need to test sagas — their state persistence, correlation-based lookup, stalled-saga recovery, and event collection — without a real database.

## Solution

Use `FakeSagaRepository` from `pydomain.testing` — an in-memory implementation of the `SagaRepository` protocol that stores saga state keyed by saga ID, supports correlation ID lookup, stalled/suspended saga queries, and collects domain events via `pull_events()`.

## Steps

### 1. Import FakeSagaRepository

```python
from pydomain.testing import FakeSagaRepository
```

### 2. Create a fake saga repository

```python
repo = FakeSagaRepository()
```

### 3. Save and retrieve saga state

```python
from uuid import UUID, uuid4


saga = OrderSaga.create(order_id="order-1")
await repo.save(saga)

loaded = await repo.get_by_id(saga.id)
assert loaded is not None
```

Returns deep copies (`model_copy(deep=True)`) on reads to prevent mutable-alias bugs.

### 4. Find by correlation ID

```python
saga = OrderSaga.create(order_id="order-1")
await repo.save(saga)

found = await repo.find_by_correlation_id(
    correlation_id=saga.correlation_id,
    saga_type="OrderSaga",
)
assert found is not None
```

### 5. Find stalled sagas

```python
stalled = await repo.find_stalled_sagas(limit=10)
for saga in stalled:
    print(f"Stalled saga: {saga.id} in status {saga.status}")
```

### 6. Find suspended and expired suspended sagas

```python
# All suspended sagas
suspended = await repo.find_suspended_sagas(limit=10)

# Only suspended sagas whose timeout has expired
expired = await repo.find_expired_suspended_sagas(limit=10)
```

### 7. Collect domain events

```python
events = repo.pull_events()
assert len(events) > 0
```

After `pull_events()`, the internal buffer is drained.

## Complete Example

```python
import pytest
from uuid import UUID, uuid4

from pydomain.testing import FakeSagaRepository


class TestOrderSaga:
    @pytest.fixture
    def repo(self) -> FakeSagaRepository:
        return FakeSagaRepository()

    async def test_save_and_load_saga(self, repo):
        saga = OrderSaga.create(order_id="order-1")
        await repo.save(saga)

        loaded = await repo.get_by_id(saga.id)
        assert loaded is not None
        assert loaded.order_id == "order-1"

    async def test_find_by_correlation_id(self, repo):
        saga = OrderSaga.create(order_id="order-1")
        await repo.save(saga)

        found = await repo.find_by_correlation_id(
            correlation_id=saga.correlation_id,
            saga_type="OrderSaga",
        )
        assert found is not None
        assert found.id == saga.id

    async def test_returns_none_for_unknown_correlation(self, repo):
        found = await repo.find_by_correlation_id(
            correlation_id=uuid4(),
            saga_type="OrderSaga",
        )
        assert found is None

    async def test_find_stalled_sagas(self, repo):
        for i in range(5):
            saga = OrderSaga.create(order_id=f"order-{i}")
            saga.mark_stalled()
            await repo.save(saga)

        stalled = await repo.find_stalled_sagas(limit=10)
        assert len(stalled) == 5

    async def test_find_suspended_sagas(self, repo):
        saga = OrderSaga.create(order_id="order-1")
        saga.suspend(reason="Waiting for payment", timeout=timedelta(minutes=5))
        await repo.save(saga)

        suspended = await repo.find_suspended_sagas(limit=10)
        assert len(suspended) == 1

    async def test_find_expired_suspended_sagas(self, repo):
        saga = OrderSaga.create(order_id="order-1")
        saga.suspend(reason="Waiting for payment", timeout=timedelta(seconds=0))
        await repo.save(saga)

        expired = await repo.find_expired_suspended_sagas(limit=10)
        assert len(expired) == 1

    async def test_pull_events(self, repo):
        saga = OrderSaga.create(order_id="order-1")
        saga.complete_step("payment_processed")
        await repo.save(saga)

        events = repo.pull_events()
        assert len(events) > 0

        # Buffer is drained after pull
        assert len(repo.pull_events()) == 0

    async def test_deep_copy_isolation(self, repo):
        saga = OrderSaga.create(order_id="order-1")
        await repo.save(saga)

        loaded = await repo.get_by_id(saga.id)
        loaded.order_id = "modified"

        # The stored copy is unchanged
        stored = await repo.get_by_id(saga.id)
        assert stored.order_id == "order-1"
```

## Expected Outcome

Your saga tests use `FakeSagaRepository` for saga state persistence. Sagas can be saved, retrieved by ID or correlation ID, queried by status (stalled, suspended, expired), and domain events are collected via `pull_events()`. Deep-copy semantics prevent test pollution through mutable aliasing.

## See Also

- [Saga Repository concept](../../concepts/sagas/saga-repository.md)
- [Define a Saga](../sagas/define-saga.md)
- [Testing philosophy](../../concepts/testing/testing-philosophy.md)
