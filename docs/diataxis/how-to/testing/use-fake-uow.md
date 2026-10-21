# How to Use a Fake Unit of Work

<!-- docs-test: runnable -->

<!-- docs-test: setup
from uuid import UUID, uuid4

from pydomain.cqrs import Command, CommandResult
from pydomain.ddd import AggregateRoot, DomainEvent


class OrderPlaced(DomainEvent):
    order_id: UUID


class CustomerRegistered(DomainEvent):
    customer_id: UUID


class Order(AggregateRoot[UUID]):
    customer_id: UUID

    @classmethod
    def place(cls, customer_id: UUID) -> "Order":
        order = cls(id=uuid4(), customer_id=customer_id)
        order._add_event(OrderPlaced(order_id=order.id))
        return order


class Customer(AggregateRoot[UUID]):
    name: str

    @classmethod
    def register(cls, name: str) -> "Customer":
        customer = cls(id=uuid4(), name=name)
        customer._add_event(CustomerRegistered(customer_id=customer.id))
        return customer


class PlaceOrderResult(CommandResult):
    order_id: UUID


class PlaceOrder(Command[PlaceOrderResult]):
    customer_id: UUID
    reject: bool = False
-->

> **Prerequisites:** [Unit of Work concept](../../concepts/cqrs/unit-of-work.md), [Fake Repository how-to](use-fake-repository.md), [Testing philosophy](../../concepts/testing/testing-philosophy.md)

## Problem

You need to test command handlers that receive a `UnitOfWork` — without hitting a real database. The UoW must manage event collection and stamping (`correlation_id` / `causation_id`) just like the production implementation, so tests reflect real behavior.

## Solution

Use `FakeUnitOfWork` from `pydomain.testing` — an in-memory Unit of Work that wraps one or more `FakeRepository` instances, performs no-op flush/commit, and inherits the real commit, rollback and event-stamping logic from `AbstractUnitOfWork`. The examples below use a synthetic `Order` aggregate that records `OrderPlaced` and a `PlaceOrder` command.

## Steps

### 1. Import FakeUnitOfWork

```python
from pydomain.testing import FakeRepository, FakeUnitOfWork
```

### 2. Create with a single repository

```python
orders = FakeRepository[Order, UUID]()
uow = FakeUnitOfWork(repository=orders)
```

### 3. Give handlers typed repository attributes

`FakeUnitOfWork(repositories={...})` registers named repositories for event collection, but it does not create attributes for them. When a handler reads `uow.orders`, as production handlers do, subclass the fake and set the attributes yourself:

```python
class OrderUoW(FakeUnitOfWork):
    def __init__(
        self,
        orders: FakeRepository[Order, UUID],
        customers: FakeRepository[Customer, UUID],
    ) -> None:
        self.orders = orders
        self.customers = customers
        super().__init__(repositories={"orders": orders, "customers": customers})
```

### 4. Test a handler through the Command Bus

The [Command Bus](../../concepts/cqrs/command-bus.md) owns the transaction: it enters the unit of work, commits it and stamps the events with the command's tracing IDs. Keep the repositories outside the factory so the test can inspect them, and let the factory build a new unit of work on every dispatch:

```python
from pydomain.cqrs import CommandBus


class PlaceOrderHandler:
    async def __call__(self, cmd: PlaceOrder, uow: OrderUoW) -> PlaceOrderResult:
        order = Order.place(cmd.customer_id)
        await uow.orders.save(order)
        if cmd.reject:
            raise ValueError("synthetic refusal")
        return PlaceOrderResult(order_id=order.id)


orders = FakeRepository[Order, UUID]()
customers = FakeRepository[Customer, UUID]()
bus = CommandBus()
bus.register(
    PlaceOrder,
    PlaceOrderHandler(),
    uow_factory=lambda: OrderUoW(orders, customers),
)

command = PlaceOrder(customer_id=uuid4())
result, events = await bus.dispatch(command)

assert await orders.get_by_id(result.order_id) is not None
assert [type(event) for event in events] == [OrderPlaced]
assert events[0].correlation_id == command.command_id
assert events[0].causation_id == command.command_id
```

### 5. Check that a refused command leaks no events

When the handler raises, the bus raises `CommandExecutionError` and the unit of work rolls back. The rollback drains the repositories' event buffers, so the refused command's `OrderPlaced` never reaches a later dispatch, even though both dispatches share the same `orders` repository:

```python
from pydomain.cqrs import CommandExecutionError

refused = PlaceOrder(customer_id=uuid4(), reject=True)
try:
    await bus.dispatch(refused)
except CommandExecutionError as error:
    assert isinstance(error.__cause__, ValueError)
else:
    raise AssertionError("the refused command must raise")

_, events = await bus.dispatch(PlaceOrder(customer_id=uuid4()))
assert len(events) == 1
```

`FakeRepository` does not undo a save on rollback: the refused order is still stored. Assert on the events, or on state your handler saves only after its checks pass.

### 6. Drive the unit of work directly

Without the bus, call `commit()` yourself. Events are collected by the commit; tracing IDs stay `None`, because the bus is what sets them:

```python
uow = OrderUoW(orders, customers)
async with uow:
    await uow.orders.save(Order.place(uuid4()))
    await uow.customers.save(Customer.register("Acme"))
    await uow.commit()

assert {type(event) for event in uow.collect_events()} == {
    OrderPlaced,
    CustomerRegistered,
}
assert uow._rolled_back is False
```

Leaving the `async with` block without `commit()` rolls back, as an exception does. `_rolled_back` records whether the current scope rolled back; it is reset each time you enter the unit of work:

```python
async with uow:
    await uow.orders.save(Order.place(uuid4()))
    # no commit

assert uow._rolled_back is True
assert uow.collect_events() == []
```

A scope commits once. Calling `commit()` a second time raises `UnitOfWorkAlreadyCommittedError`, and the first commit's changes stay persisted; enter the unit of work again, or call `rollback()`, to start a new scope:

```python
from pydomain.cqrs import UnitOfWorkAlreadyCommittedError

async with uow:
    await uow.commit()
    try:
        await uow.commit()
    except UnitOfWorkAlreadyCommittedError:
        pass
    else:
        raise AssertionError("a second commit must raise")
```

`collect_events()` returns a new list each time, so the list you hold is not changed by a later scope on the same unit of work.

## Complete Example

A pytest module that combines the steps, using `OrderUoW` and `PlaceOrderHandler` from above:

```python
import pytest

from pydomain.cqrs import CommandBus, CommandExecutionError


@pytest.fixture
def orders() -> FakeRepository[Order, UUID]:
    return FakeRepository()


@pytest.fixture
def bus(orders: FakeRepository[Order, UUID]) -> CommandBus:
    customers = FakeRepository[Customer, UUID]()
    bus = CommandBus()
    bus.register(
        PlaceOrder,
        PlaceOrderHandler(),
        uow_factory=lambda: OrderUoW(orders, customers),
    )
    return bus


@pytest.mark.anyio
async def test_place_order_stores_the_order(
    bus: CommandBus, orders: FakeRepository[Order, UUID]
) -> None:
    result, events = await bus.dispatch(PlaceOrder(customer_id=uuid4()))

    assert await orders.get_by_id(result.order_id) is not None
    assert [type(event) for event in events] == [OrderPlaced]


@pytest.mark.anyio
async def test_refused_order_publishes_nothing(bus: CommandBus) -> None:
    with pytest.raises(CommandExecutionError):
        await bus.dispatch(PlaceOrder(customer_id=uuid4(), reject=True))

    _, events = await bus.dispatch(PlaceOrder(customer_id=uuid4()))
    assert len(events) == 1
```

<!-- docs-test: check
from tests.docs.doc_examples import run_tests

await run_tests(
    test_place_order_stores_the_order,
    test_refused_order_publishes_nothing,
)
-->

## When to Use FakeUnitOfWork vs Direct Repository

| Scenario | Use |
|----------|-----|
| Testing a single repository method | `FakeRepository` directly |
| Testing a command handler that receives a UoW | `FakeUnitOfWork`, through the `CommandBus` |
| Testing event collection and stamping | `FakeUnitOfWork` through the `CommandBus` (with the returned events) |
| Testing multi-aggregate transactions | A `FakeUnitOfWork` subclass with typed repository attributes |

## Expected Outcome

Your command handler tests use `FakeUnitOfWork` with in-memory repositories. Events are collected on commit and stamped with tracing IDs by the bus, and a refused command's events never reach a later commit. Tests exercise the full UoW lifecycle — enter, commit (or rollback), exit — without a database.

## See Also

- [Unit of Work concept](../../concepts/cqrs/unit-of-work.md)
- [Use a Fake Repository](use-fake-repository.md)
- [Run a Contract Suite Against Your Adapter](run-contract-suites.md) — `UnitOfWorkContract`
- [Testing philosophy](../../concepts/testing/testing-philosophy.md)
