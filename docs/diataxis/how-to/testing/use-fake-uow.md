# How to Use a Fake Unit of Work

> **Prerequisites:** [Unit of Work concept](../../concepts/cqrs/unit-of-work.md), [Fake Repository how-to](use-fake-repository.md), [Testing philosophy](../../concepts/testing/testing-philosophy.md)

## Problem

You need to test command handlers that receive a `UnitOfWork` — without hitting a real database. The UoW must manage event collection and stamping (`correlation_id` / `causation_id`) just like the production implementation, so tests reflect real behavior.

## Solution

Use `FakeUnitOfWork` from `pydomain.testing` — an in-memory Unit of Work that wraps one or more `FakeRepository` instances, performs no-op flush/commit, and inherits the real event-stamping logic from `AbstractUnitOfWork`.

## Steps

### 1. Import FakeUnitOfWork

```python
from pydomain.testing import FakeUnitOfWork, FakeRepository
```

### 2. Create with a single repository

```python
repo: FakeRepository[Order, UUID] = FakeRepository()
uow = FakeUnitOfWork(repository=repo)
```

### 3. Create with multiple named repositories

```python
order_repo = FakeRepository[Order, UUID]()
customer_repo = FakeRepository[Customer, UUID]()

uow = FakeUnitOfWork(repositories={
    "orders": order_repo,
    "customers": customer_repo,
})
```

### 4. Use in a command handler test

```python
class PlaceOrderHandler:
    async def __call__(self, cmd: PlaceOrder, uow: AbstractUnitOfWork) -> PlaceOrderResult:
        order = Order.create(customer_id=cmd.customer_id, items=cmd.items)
        await uow.orders.save(order)  # access named repo
        return PlaceOrderResult(order_id=order.id)


async def test_place_order():
    repo = FakeRepository()
    uow = FakeUnitOfWork(repository=repo)
    handler = PlaceOrderHandler()

    result = await handler(PlaceOrder(customer_id="c1", items=[]), uow)

    saved = await repo.get_by_id(result.order_id)
    assert saved is not None
```

### 5. Verify event collection after commit

The UoW's `commit()` calls `_collect_and_stamp()`, which drains events from all tracked repositories and stamps them with tracing IDs:

```python
async def test_uow_collects_and_stamps_events():
    repo = FakeRepository()
    uow = FakeUnitOfWork(repository=repo)
    handler = PlaceOrderHandler(repo)

    async with uow:
        await handler(PlaceOrder(customer_id="c1", items=[]), uow)

    # After __aexit__ → commit() → events collected and stamped
    events = uow.collect_events()
    assert len(events) > 0
    assert all(e.correlation_id is not None for e in events)
    assert all(e.causation_id is not None for e in events)
```

### 6. Verify rollback behavior

```python
async def test_rollback_flag():
    repo = FakeRepository()
    uow = FakeUnitOfWork(repository=repo)

    async with uow:
        await repo.save(Order.create(customer_id="c1", items=[]))
        await uow.rollback()

    assert uow._rolled_back is True
```

## Complete Example

```python
import pytest
from uuid import UUID

from pydomain.testing import FakeUnitOfWork, FakeRepository


class TestWithFakeUnitOfWork:
    @pytest.fixture
    def repo(self) -> FakeRepository[Order, UUID]:
        return FakeRepository()

    @pytest.fixture
    def uow(self, repo) -> FakeUnitOfWork:
        return FakeUnitOfWork(repository=repo)

    @pytest.fixture
    def handler(self, repo) -> PlaceOrderHandler:
        return PlaceOrderHandler(repo)

    async def test_handler_with_uow(self, handler, uow, repo):
        async with uow:
            result = await handler(
                PlaceOrder(customer_id="c1", items=[OrderItem("widget", 3)]),
                uow,
            )
        # UoW exited → commit() called → events collected

        saved = await repo.get_by_id(result.order_id)
        assert saved.customer_id == "c1"

        events = uow.collect_events()
        assert any(isinstance(e, OrderPlaced) for e in events)
        # Events are stamped with tracing IDs
        assert events[0].correlation_id is not None

    async def test_multiple_aggregates_in_one_transaction(self):
        orders = FakeRepository[Order, UUID]()
        customers = FakeRepository[Customer, UUID]()

        uow = FakeUnitOfWork(repositories={
            "orders": orders,
            "customers": customers,
        })

        async with uow:
            customer = Customer.create(name="Acme")
            await uow.customers.save(customer)
            order = Order.create(customer_id=str(customer.id), items=[])
            await uow.orders.save(order)

        assert await uow.orders.get_by_id(order.id) is not None
        assert await uow.customers.get_by_id(customer.id) is not None

        # Events from both repos are collected
        events = uow.collect_events()
        assert len(events) >= 2

    async def test_exception_triggers_rollback(self, repo):
        uow = FakeUnitOfWork(repository=repo)

        with pytest.raises(ValueError):
            async with uow:
                await repo.save(Order.create(customer_id="c1", items=[]))
                raise ValueError("something failed")

        assert uow._rolled_back is True
```

## When to Use FakeUnitOfWork vs Direct Repository

| Scenario | Use |
|----------|-----|
| Testing a single repository method | `FakeRepository` directly |
| Testing a command handler that receives a UoW | `FakeUnitOfWork` |
| Testing event collection and stamping | `FakeUnitOfWork` (with `collect_events()`) |
| Testing multi-aggregate transactions | `FakeUnitOfWork` with named repositories |

## Expected Outcome

Your command handler tests use `FakeUnitOfWork` with in-memory repositories. Events are properly collected and stamped with tracing IDs. Tests exercise the full UoW lifecycle — enter, commit (or rollback), exit — without a database.

## See Also

- [Unit of Work concept](../../concepts/cqrs/unit-of-work.md)
- [Use a Fake Repository](use-fake-repository.md)
- [Testing philosophy](../../concepts/testing/testing-philosophy.md)
