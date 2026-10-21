# Recipe: Test Your Application

<!-- docs-test: runnable -->

> **Adoption Level:** All levels · Prerequisites: All module concepts and how-tos, [Testing philosophy concept](../../concepts/testing/testing-philosophy.md)

This recipe catalogs every fake and in-memory double in `pydomain.testing`, shows when to use each one, and provides complete test examples for each architectural layer.

## Fake Inventory

| Class | Implements | Use For |
|-------|-----------|---------|
| `FakeRepository` | `Repository[T, TId]` | Testing command handlers that persist aggregates |
| `FakeUnitOfWork` | `AbstractUnitOfWork` | Wrapping transaction boundaries with `FakeRepository` |
| `FakeEventStore` | `EventStore` | Testing event-sourced aggregates and ES repositories |
| `FakeSnapshotStore` | `SnapshotStore` | Testing snapshot creation and recovery |
| `FakeSagaRepository` | `SagaRepository` | Testing saga lifecycle, state hydration, recovery |
| `FakeCheckpointStore` | `CheckpointStore` | Testing catch-up subscriptions and projections |
| `FakeLockProvider` | `LockProvider` | Testing distributed locking behaviors |
| `FakeProcessedMessageStore` | `ProcessedMessageStore` | Testing idempotency behavior |
| `InMemoryMessageBroker` | `MessageBroker` | Testing integration event publishing |
| `InMemoryMessageSubscriber` | `MessageSubscriber` | Testing external message consumption |
| `InMemoryProjectionStore` | `ProjectionStore` | Testing read model projections |

All fakes are **in-memory** — no database, no broker, no external process. Tests run in milliseconds and are fully deterministic.

## The Domain Under Test

The examples share a small synthetic domain: an `Order` aggregate that records `OrderPlaced`, a `PlaceOrder` command and its handler. Each test is a plain function that builds what it needs, so you can copy one without a fixture. `pytestmark = pytest.mark.anyio` makes pytest run the `async` tests through the `anyio` pytest plugin, which ships with `anyio`; the docs runner awaits them itself.

```python
from uuid import UUID, uuid4

import pytest

from pydomain.cqrs import Command, CommandResult
from pydomain.ddd import AggregateRoot, DomainError, DomainEvent

pytestmark = pytest.mark.anyio


class OrderNotModifiable(DomainError):
    """Raised when an order that is not a draft is changed."""


class OrderPlaced(DomainEvent):
    order_id: UUID
    total_amount: int


class Order(AggregateRoot[UUID]):
    customer_id: UUID
    total_amount: int = 0
    status: str = "draft"

    def add_item(self, quantity: int, unit_price: int) -> None:
        if self.status != "draft":
            raise OrderNotModifiable(f"Order is {self.status}")
        self.total_amount += quantity * unit_price

    def place(self) -> None:
        if self.status != "draft":
            raise OrderNotModifiable(f"Order is {self.status}")
        self.status = "placed"
        self._add_event(OrderPlaced(order_id=self.id, total_amount=self.total_amount))


class PlaceOrderResult(CommandResult):
    order_id: UUID
    status: str


class PlaceOrder(Command[PlaceOrderResult]):
    order_id: UUID
    customer_id: UUID
    quantity: int
    unit_price: int


class PlaceOrderHandler:
    def __init__(self, repository) -> None:
        self._repository = repository
        self.calls = 0

    async def __call__(self, command: PlaceOrder, uow) -> PlaceOrderResult:
        self.calls += 1
        order = Order(id=command.order_id, customer_id=command.customer_id)
        order.add_item(command.quantity, command.unit_price)
        order.place()
        await self._repository.save(order)
        return PlaceOrderResult(order_id=order.id, status=order.status)
```

## DDD Layer Testing

Test entities, value objects, and aggregates in isolation. An aggregate needs no fake; `FakeRepository` covers saving and loading.

```python
from pydomain.ddd import ConcurrencyError
from pydomain.testing import FakeRepository


def test_place_records_event():
    order = Order(id=uuid4(), customer_id=uuid4())
    order.add_item(quantity=2, unit_price=500)
    order.place()

    assert order.status == "placed"
    events = order.pull_events()
    assert len(events) == 1
    assert events[0].total_amount == 1000


def test_mutation_guards_raise_domain_errors():
    order = Order(id=uuid4(), customer_id=uuid4())
    order.place()

    with pytest.raises(OrderNotModifiable):
        order.add_item(quantity=1, unit_price=200)


async def test_save_and_retrieve():
    repo: FakeRepository[Order, UUID] = FakeRepository()
    order = Order(id=uuid4(), customer_id=uuid4())

    await repo.save(order)
    found = await repo.get_by_id(order.id)

    assert found is not None
    assert found.id == order.id


async def test_concurrency_conflict():
    order = Order(id=uuid4(), customer_id=uuid4())
    repo: FakeRepository[Order, UUID] = FakeRepository(aggregates=[order])

    # The fake stores the object it is given, so a second writer needs its own
    # copy of the aggregate, loaded at the same version.
    other_writer = order.model_copy()
    order.add_item(1, 100)
    await repo.save(order)  # the stored version moves on to 1

    with pytest.raises(ConcurrencyError):
        await repo.save(other_writer)  # still at version 0: stale


async def test_event_collection():
    repo: FakeRepository[Order, UUID] = FakeRepository()
    order = Order(id=uuid4(), customer_id=uuid4())
    order.add_item(1, 100)
    order.place()

    await repo.save(order)
    events = repo.pull_events()
    assert len(events) == 1
    assert isinstance(events[0], OrderPlaced)

    # Buffer is drained after pull
    assert repo.pull_events() == []
```

<!-- docs-test: check
test_place_records_event()
test_mutation_guards_raise_domain_errors()
await test_save_and_retrieve()
await test_concurrency_conflict()
await test_event_collection()
-->

## CQRS Layer Testing

Test command handlers, pipeline behaviors and the unit of work with `FakeUnitOfWork`, `FakeProcessedMessageStore` and `FakeLockProvider`. A handler is a callable, so call it directly with a unit of work; to test a behavior, register it on the bus `bootstrap()` builds and dispatch through `app.dispatch()`.

```python
from pydomain.cqrs import AggregateLockingBehavior, IdempotencyBehavior
from pydomain.cqrs.locking import DictLockKeyResolver
from pydomain.infrastructure import bootstrap
from pydomain.testing import (
    FakeLockProvider,
    FakeProcessedMessageStore,
    FakeRepository,
    FakeUnitOfWork,
)


def a_command() -> PlaceOrder:
    return PlaceOrder(order_id=uuid4(), customer_id=uuid4(), quantity=1, unit_price=500)


async def test_place_order_returns_result():
    repo: FakeRepository[Order, UUID] = FakeRepository()
    handler = PlaceOrderHandler(repo)
    command = a_command()

    result = await handler(command, FakeUnitOfWork(repository=repo))

    assert result.status == "placed"
    assert await repo.get_by_id(result.order_id) is not None


async def test_domain_events_collected_via_uow():
    repo: FakeRepository[Order, UUID] = FakeRepository()
    handler = PlaceOrderHandler(repo)
    uow = FakeUnitOfWork(repository=repo)

    async with uow:
        await handler(a_command(), uow)
        # The command bus commits in production; a direct test commits itself.
        # commit() pulls the events from every repository. A scope commits once.
        await uow.commit()

    assert len(uow.collect_events()) == 1


async def test_duplicate_command_returns_cached_result():
    repo: FakeRepository[Order, UUID] = FakeRepository()
    handler = PlaceOrderHandler(repo)
    store = FakeProcessedMessageStore()
    app = await bootstrap()
    app.message_bus.register_command(
        PlaceOrder, handler, lambda: FakeUnitOfWork(repository=repo),
        behaviors=[IdempotencyBehavior(store)],
    )
    command = a_command()

    first = await app.dispatch(command)
    second = await app.dispatch(command)  # same command_id

    assert second == first
    assert handler.calls == 1
    assert await store.contains(command.command_id)
    await app.shutdown()


async def test_lock_is_released_after_the_command():
    repo: FakeRepository[Order, UUID] = FakeRepository()
    locks = FakeLockProvider()
    resolver = DictLockKeyResolver()
    resolver.register(PlaceOrder, lambda cmd: [f"order-{cmd.order_id}"])
    app = await bootstrap()
    app.message_bus.register_command(
        PlaceOrder, PlaceOrderHandler(repo), lambda: FakeUnitOfWork(repository=repo),
        behaviors=[AggregateLockingBehavior(locks, resolver)],
    )
    command = a_command()

    await app.dispatch(command)

    # A released key can be taken again.
    await locks.acquire(f"order-{command.order_id}")
    await locks.release(f"order-{command.order_id}")
    await app.shutdown()
```

<!-- docs-test: check
await test_place_order_returns_result()
await test_domain_events_collected_via_uow()
await test_duplicate_command_returns_cached_result()
await test_lock_is_released_after_the_command()
-->

## Event Sourcing Layer Testing

Test the event store, snapshots and subscription checkpoints with `FakeEventStore`, `FakeSnapshotStore` and `FakeCheckpointStore`. The store takes the aggregate id as a string and the events the aggregate recorded.

```python
from pydomain.es import DuplicateCommandError, Snapshot
from pydomain.testing import FakeCheckpointStore, FakeEventStore, FakeSnapshotStore


def placed(order_id: UUID, total_amount: int = 100) -> OrderPlaced:
    return OrderPlaced(order_id=order_id, total_amount=total_amount)


async def test_append_and_replay():
    store = FakeEventStore()
    order_id = uuid4()

    await store.append_to_stream(
        str(order_id), [placed(order_id, 1000)], expected_version=0, command_id=uuid4()
    )

    stream = await store.read_stream(str(order_id))
    assert len(stream.events) == 1
    assert isinstance(stream.events[0], OrderPlaced)
    assert stream.version == 1


async def test_concurrency_on_append():
    store = FakeEventStore()
    order_id = uuid4()
    await store.append_to_stream(str(order_id), [placed(order_id)], expected_version=0)

    with pytest.raises(ConcurrencyError):
        await store.append_to_stream(
            str(order_id), [placed(order_id, 200)],
            expected_version=0,  # stale: the stream already has 1 event
        )


async def test_deduplication_on_duplicate_command():
    store = FakeEventStore()
    order_id = uuid4()
    command_id = uuid4()
    await store.append_to_stream(
        str(order_id), [placed(order_id)], expected_version=0, command_id=command_id
    )

    with pytest.raises(DuplicateCommandError):
        await store.append_to_stream(
            str(order_id), [placed(order_id, 200)],
            expected_version=1,
            command_id=command_id,  # the same command_id again
        )


async def test_save_and_load_snapshot():
    store = FakeSnapshotStore()
    snapshot = Snapshot(
        aggregate_id="order-1", version=5, state={"status": "placed", "total_amount": 1000}
    )
    await store.save("Order", snapshot)

    loaded = await store.get("Order", "order-1")
    assert loaded is not None
    assert loaded.version == 5
    assert loaded.state["status"] == "placed"


async def test_missing_snapshot_returns_none():
    store = FakeSnapshotStore()
    assert await store.get("Order", "nonexistent") is None


async def test_save_and_load_checkpoint():
    store = FakeCheckpointStore()

    assert await store.load("my-subscription") == 0  # default

    await store.save("my-subscription", 42)
    assert await store.load("my-subscription") == 42
```

<!-- docs-test: check
await test_append_and_replay()
await test_concurrency_on_append()
await test_deduplication_on_duplicate_command()
await test_save_and_load_snapshot()
await test_missing_snapshot_returns_none()
await test_save_and_load_checkpoint()
-->

## Saga Layer Testing

Test a saga end to end with `SagaManager` and `FakeSagaRepository`, and its state with `SagaState` directly. The manager sends its commands through a dispatcher that also publishes the events those commands raise, which is the `MessageBus`; a `CommandBus` is refused with `TypeError` ([ADR-077](../../../adr/ADR-077-saga-failure-and-recovery-keep-every-command.md), [Configure a Saga Manager](../sagas/configure-saga-manager.md)). `FakeSagaRepository` stores a deep copy on `save()` and returns a deep copy from every read, so changing a loaded state never changes what is stored.

```python
from pydomain.cqrs import EmptyCommandResult
from pydomain.cqrs.saga import Saga, SagaManager, SagaRegistry, SagaState, SagaStatus
from pydomain.infrastructure import MessageBus
from pydomain.testing import FakeSagaRepository


class StockReserved(DomainEvent):
    order_id: UUID


class ReserveStock(Command[EmptyCommandResult]):
    order_id: UUID


class ConfirmOrder(Command[EmptyCommandResult]):
    order_id: UUID


class OrderFulfillmentSaga(Saga[SagaState]):
    """Reserve stock when an order is placed, confirm it when the stock is reserved."""

    listens_to = [OrderPlaced, StockReserved]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(OrderPlaced, send=lambda e: ReserveStock(order_id=e.order_id), step="reserving")
        self.on(StockReserved, send=lambda e: ConfirmOrder(order_id=e.order_id), step="confirming")


async def test_saga_runs_to_confirmation():
    saga_repo = FakeSagaRepository()
    registry = SagaRegistry()
    registry.register_saga(OrderFulfillmentSaga)
    bus = MessageBus()
    manager = SagaManager(repository=saga_repo, registry=registry, command_bus=bus)
    manager.bind_to(bus)

    class Reservation(AggregateRoot[UUID]):
        order_id: UUID

        def reserve(self) -> None:
            self._add_event(StockReserved(order_id=self.order_id))

    reservations: FakeRepository[Reservation, UUID] = FakeRepository()
    confirmed: list[UUID] = []

    async def reserve_stock(command: ReserveStock, uow) -> EmptyCommandResult:
        reservation = Reservation(id=uuid4(), order_id=command.order_id)
        reservation.reserve()
        await reservations.save(reservation)
        return EmptyCommandResult()

    async def confirm_order(command: ConfirmOrder, uow) -> EmptyCommandResult:
        confirmed.append(command.order_id)
        return EmptyCommandResult()

    bus.register_command(ReserveStock, reserve_stock, lambda: FakeUnitOfWork(reservations))
    bus.register_command(ConfirmOrder, confirm_order, lambda: FakeUnitOfWork(reservations))

    correlation_id, order_id = uuid4(), uuid4()
    await bus.dispatch(OrderPlaced(order_id=order_id, total_amount=100, correlation_id=correlation_id))

    assert confirmed == [order_id]
    state = await saga_repo.find_by_correlation_id(correlation_id, "OrderFulfillmentSaga")
    assert state is not None
    assert state.saga_type == "OrderFulfillmentSaga"


async def test_state_deep_copy_isolation():
    """FakeSagaRepository returns deep copies, so mutations do not leak."""
    repo = FakeSagaRepository()
    state = SagaState(id=uuid4(), saga_type="TestSaga", correlation_id=uuid4())
    await repo.save(state)

    loaded = await repo.get_by_id(state.id)
    loaded.current_step = "modified"

    reloaded = await repo.get_by_id(state.id)
    assert reloaded.current_step == "init"  # original preserved


async def test_find_by_correlation_id():
    repo = FakeSagaRepository()
    correlation_id = uuid4()
    await repo.save(SagaState(id=uuid4(), saga_type="TestSaga", correlation_id=correlation_id))

    found = await repo.find_by_correlation_id(correlation_id, "TestSaga")
    assert found is not None
    assert await repo.find_by_correlation_id(correlation_id, "OtherSaga") is None


def test_mark_event_processed_is_idempotent():
    state = SagaState(id=uuid4(), saga_type="TestSaga")
    event_id = uuid4()

    assert not state.is_event_processed(event_id)
    state.mark_event_processed(event_id)
    state.mark_event_processed(event_id)
    assert state.is_event_processed(event_id)


def test_terminal_states():
    state = SagaState(id=uuid4(), saga_type="TestSaga")

    state.status = SagaStatus.RUNNING
    assert not state.is_terminal

    state.status = SagaStatus.COMPLETED
    assert state.is_terminal


def test_step_history_grows():
    state = SagaState(id=uuid4(), saga_type="TestSaga")

    for i in range(5):
        state.record_step(
            step_name=f"step_{i}", event_type=f"Event{i}", causation_id=uuid4()
        )

    assert len(state.step_history) == 5
    assert state.current_step == "step_4"
```

<!-- docs-test: check
await test_saga_runs_to_confirmation()
await test_state_deep_copy_isolation()
await test_find_by_correlation_id()
test_mark_event_processed_is_idempotent()
test_terminal_states()
test_step_history_grows()
-->

## Infrastructure Layer Testing

Test message publishing and subscription with `InMemoryMessageBroker`, `InMemoryMessageSubscriber` and `InMemoryProjectionStore`. The broker records `(topic, event, headers)` tuples in `broker.published`.

```python
from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.testing import (
    InMemoryMessageBroker,
    InMemoryMessageSubscriber,
    InMemoryProjectionStore,
)


class OrderShipped(IntegrationEvent):
    order_id: str
    tracking_number: str


async def test_publish_captures_topic_event_and_headers():
    broker = InMemoryMessageBroker()

    event = OrderShipped(order_id="o1", tracking_number="TRACK-1")
    await broker.publish("orders", event)

    assert len(broker.published) == 1
    topic, captured, headers = broker.published[0]
    assert topic == "orders"
    assert captured.order_id == "o1"
    assert headers == {}


async def test_multiple_publishes_accumulate():
    broker = InMemoryMessageBroker()
    await broker.publish("orders", OrderShipped(order_id="o1", tracking_number="A"))
    await broker.publish("orders", OrderShipped(order_id="o2", tracking_number="B"))
    assert len(broker.published) == 2


async def test_simulate_message_invokes_handler():
    subscriber = InMemoryMessageSubscriber()
    received: list[dict] = []

    async def handler(payload: dict) -> None:
        received.append(payload)

    subscriber.subscribe("orders", handler)
    await subscriber.simulate_message("orders", {"order_id": "o1"})

    assert received == [{"order_id": "o1"}]


async def test_start_stop_flags():
    subscriber = InMemoryMessageSubscriber()
    assert not subscriber.started

    await subscriber.start()
    assert subscriber.started

    await subscriber.stop()
    assert subscriber.stopped


async def test_simulate_unregistered_topic_raises():
    subscriber = InMemoryMessageSubscriber()
    with pytest.raises(KeyError):
        await subscriber.simulate_message("unknown", {})


async def test_projection_store_save_and_load():
    store = InMemoryProjectionStore()

    await store.save("order-summary", {"total": 42, "count": 7})

    assert await store.load("order-summary") == {"total": 42, "count": 7}


async def test_missing_projection_returns_none():
    store = InMemoryProjectionStore()
    assert await store.load("nonexistent") is None
```

<!-- docs-test: check
await test_publish_captures_topic_event_and_headers()
await test_multiple_publishes_accumulate()
await test_simulate_message_invokes_handler()
await test_start_stop_flags()
await test_simulate_unregistered_topic_raises()
await test_projection_store_save_and_load()
await test_missing_projection_returns_none()
-->

## Full Integration Test

Assemble the layers in one test: a command goes through `app.dispatch()`, the handler saves the aggregate, and an event handler publishes an integration event to an `InMemoryMessageBroker` after the commit.

```python
async def test_place_order_publishes_integration_event():
    repo: FakeRepository[Order, UUID] = FakeRepository()
    broker = InMemoryMessageBroker()

    async def publish_shipped(event: OrderPlaced) -> None:
        await broker.publish(
            "orders",
            OrderShipped(order_id=str(event.order_id), tracking_number="TRACK-1"),
        )

    app = await bootstrap(message_broker=broker)
    app.message_bus.register_command(
        PlaceOrder, PlaceOrderHandler(repo), lambda: FakeUnitOfWork(repository=repo)
    )
    app.message_bus.register_event(OrderPlaced, publish_shipped)

    try:
        result = await app.dispatch(a_command())  # dispatch() returns the command result
    finally:
        await app.shutdown()

    assert result.status == "placed"
    assert await repo.get_by_id(result.order_id) is not None
    assert [topic for topic, _, _ in broker.published] == ["orders"]
```

<!-- docs-test: check
await test_place_order_publishes_integration_event()
-->

The test registers everything on `app.message_bus`, the bus `bootstrap()` built, and shuts the application down in a `finally` block. `shutdown()` stops the inbound gateways, the outbound gateway and the broker, runs every stop even when one raises, and re-raises the first error, so a failing fake shows up in the test instead of leaving the others running. If the test wires an `InboundEventGateway`, build it on a `MessageBus` you create and pass that bus to `bootstrap(message_bus=bus, inbound_gateways=[gateway])`; `bootstrap()` raises `ValueError` for a gateway built on any other bus ([ADR-073](../../../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md)).

## Testing Patterns Summary

| Layer | Arrange | Act | Assert |
|-------|---------|-----|--------|
| **DDD** | `Order(...)`, `FakeRepository(aggregates=[...])` | An aggregate method, `repo.save()` | `repo.get_by_id()`, `repo.pull_events()` |
| **CQRS** | `FakeUnitOfWork(repository=repo)`, `bootstrap()` for behaviors | `handler(cmd, uow)` inside `async with uow`, then `uow.commit()`; or `app.dispatch(cmd)` | `uow.collect_events()`, handler result, `store.contains()` |
| **ES** | `FakeEventStore()` | `store.append_to_stream()` | `store.read_stream()`, stream events |
| **Saga** | `FakeSagaRepository()`, `MessageBus()`, `SagaManager` | `bus.dispatch(event)`, `repo.save(state)`, `state.record_step()` | `repo.get_by_id()`, `repo.find_by_correlation_id()`, `state.step_history` |
| **Infra** | `InMemoryMessageBroker()` | `broker.publish(topic, event)` | `broker.published` (`(topic, event, headers)` tuples) |

## What's Next?

- [All Modules Integration recipe](all-modules.md) — full monolith with DDD + CQRS + ES + Sagas
- [Use a Fake Repository how-to](../testing/use-fake-repository.md) — deep dive on `FakeRepository`
- [Use a Fake Saga Repository how-to](../testing/use-fake-saga-repository.md) — deep dive on `FakeSagaRepository`
- [Handle Domain Errors how-to](../ddd/handle-domain-errors.md) — test error propagation
