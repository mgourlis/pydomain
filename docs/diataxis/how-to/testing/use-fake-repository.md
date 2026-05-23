# How to Use a Fake Repository

> **Prerequisites:** [Repository concept](../../concepts/ddd/repositories.md), [Implement a Repository](../ddd/implement-repository.md), [Testing philosophy](../../concepts/testing/testing-philosophy.md)

## Problem

You need to test command handlers and domain logic that depend on a `Repository` without connecting to a real database. Tests must run fast, be deterministic, and support event collection for verifying domain events.

## Solution

Use `FakeRepository` from `pydomain.testing` — an in-memory implementation of the `Repository` protocol that performs optimistic concurrency checks, tracks seen aggregates, and collects domain events via `pull_events()`.

## Steps

### 1. Import FakeRepository

```python
from pydomain.testing import FakeRepository
```

### 2. Create a fake repository

```python
from uuid import UUID

from pydomain.testing import FakeRepository


# Empty repository
repo: FakeRepository[Order, UUID] = FakeRepository()

# Pre-populated with existing aggregates
existing_order = Order.create(customer_id="c1", items=[...])
repo = FakeRepository(aggregates=[existing_order])
```

### 3. Pass it to a command handler

```python
class PlaceOrderHandler:
    def __init__(self, repository: Repository[Order, UUID]) -> None:
        self._repo = repository

    async def __call__(self, cmd: PlaceOrder, uow: AbstractUnitOfWork) -> PlaceOrderResult:
        order = Order.create(customer_id=cmd.customer_id, items=cmd.items)
        await self._repo.save(order)
        return PlaceOrderResult(order_id=order.id)


# In tests:
repo = FakeRepository()
handler = PlaceOrderHandler(repo)
```

### 4. Verify saved aggregates

```python
async def test_place_order_saves_aggregate():
    repo = FakeRepository()
    handler = PlaceOrderHandler(repo)

    result = await handler(PlaceOrder(customer_id="c1", items=[]), uow)

    saved = await repo.get_by_id(result.order_id)
    assert saved is not None
    assert saved.customer_id == "c1"
```

### 5. Verify domain events via pull_events()

```python
async def test_place_order_records_event():
    repo = FakeRepository()
    handler = PlaceOrderHandler(repo)

    await handler(PlaceOrder(customer_id="c1", items=[]), uow)

    events = repo.pull_events()
    assert len(events) == 1
    assert isinstance(events[0], OrderPlaced)
    assert events[0].customer_id == "c1"
```

After `pull_events()`, the internal buffer is drained — a second call returns an empty list.

### 6. Pre-populate for existing-aggregate scenarios

```python
async def test_cancel_order():
    order = Order.create(customer_id="c1", items=[item])
    repo = FakeRepository(aggregates=[order])
    handler = CancelOrderHandler(repo)

    await handler(CancelOrder(order_id=order.id), uow)

    updated = await repo.get_by_id(order.id)
    assert updated.status == OrderStatus.CANCELLED
```

### 7. Test optimistic concurrency

```python
import pytest
from pydomain.ddd.exceptions import ConcurrencyError


async def test_concurrency_conflict():
    order = Order.create(customer_id="c1", items=[item])
    repo = FakeRepository(aggregates=[order])

    # Simulate concurrent update: load, modify, save
    a1 = await repo.get_by_id(order.id)
    a2 = await repo.get_by_id(order.id)
    a1.add_item(Product("widget", 10))
    await repo.save(a1)  # bumps version
    a2.add_item(Product("gadget", 5))
    with pytest.raises(ConcurrencyError):
        await repo.save(a2)  # stale version → conflict
```

## Complete Example

```python
import pytest
from uuid import UUID

from pydomain.testing import FakeRepository, FakeUnitOfWork
from pydomain.ddd.exceptions import ConcurrencyError


class TestPlaceOrderHandler:
    @pytest.fixture
    def repo(self) -> FakeRepository[Order, UUID]:
        return FakeRepository()

    @pytest.fixture
    def uow(self, repo) -> FakeUnitOfWork:
        return FakeUnitOfWork(repository=repo)

    @pytest.fixture
    def handler(self, repo) -> PlaceOrderHandler:
        return PlaceOrderHandler(repo)

    async def test_saves_new_order(self, handler, uow, repo):
        result = await handler(
            PlaceOrder(customer_id="c1", items=[OrderItem("widget", 2)]),
            uow,
        )

        saved = await repo.get_by_id(result.order_id)
        assert saved.customer_id == "c1"

    async def test_records_order_placed_event(self, handler, uow, repo):
        await handler(PlaceOrder(customer_id="c1", items=[]), uow)

        events = repo.pull_events()
        assert any(isinstance(e, OrderPlaced) for e in events)

    async def test_concurrent_modification_raises(self, repo):
        order = Order.create(customer_id="c1", items=[])
        repo = FakeRepository(aggregates=[order])

        a1 = await repo.get_by_id(order.id)
        a2 = await repo.get_by_id(order.id)
        a1.add_item(OrderItem("widget", 1))
        await repo.save(a1)
        with pytest.raises(ConcurrencyError):
            await repo.save(a2)

    async def test_delete_is_idempotent(self, repo):
        order = Order.create(customer_id="c1", items=[])
        repo = FakeRepository(aggregates=[order])

        await repo.delete(order.id)
        assert await repo.get_by_id(order.id) is None

        # Deleting again doesn't raise
        await repo.delete(order.id)
```

## Expected Outcome

Your tests use `FakeRepository` instead of a real database. Aggregates are stored in memory, domain events are collected via `pull_events()`, and optimistic concurrency is enforced. Tests run in milliseconds.

## See Also

- [Repository concept](../../concepts/ddd/repositories.md)
- [Implement a Repository](../ddd/implement-repository.md)
- [Use a Fake UoW](use-fake-uow.md)
- [Testing philosophy](../../concepts/testing/testing-philosophy.md)
