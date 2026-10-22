# How to Configure a Saga Manager

<!-- docs-test: runnable -->

> **Adoption Level:** 5 · Prerequisites: [Saga Manager concept](../../concepts/sagas/saga-manager.md), [Saga Registry concept](../../concepts/sagas/saga-registry.md), [Command Bus concept](../../concepts/cqrs/command-bus.md)

This guide shows how to wire the `SagaManager` with its dependencies — repository, registry and a dispatcher — and integrate it with the application's `MessageBus`.

## 1. Create the dependencies

```python
from pydomain.cqrs.saga import SagaManager, SagaRegistry
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.testing import FakeSagaRepository

# The repository persists SagaState (FakeSagaRepository for tests and examples)
repo = FakeSagaRepository()

# The registry maps event types to saga classes
registry = SagaRegistry()

# The MessageBus runs the commands sagas produce and publishes their events
message_bus = MessageBus()
```

The manager sends its commands through a `CommandDispatcher`: an object whose `async dispatch(command)` runs the command **and publishes the domain events it raised**. The `MessageBus` does. A `CommandBus` does not (it returns the events to its caller), so a saga wired to it would never see the events its own commands cause; `SagaManager` raises `TypeError` for a `CommandBus` or a subclass of it ([ADR-077](../../../adr/ADR-077-saga-failure-and-recovery-keep-every-command.md)).

## 2. Define and register your saga classes

A synthetic order saga: reserve stock when an order is placed, confirm the order when the stock is reserved.

```python
from pydomain.cqrs.commands import Command, EmptyCommandResult
from pydomain.cqrs.saga import Saga, SagaState
from pydomain.ddd.domain_event import DomainEvent


class OrderPlaced(DomainEvent):
    order_id: str


class StockReserved(DomainEvent):
    order_id: str


class ReserveStock(Command[EmptyCommandResult]):
    order_id: str


class ConfirmOrder(Command[EmptyCommandResult]):
    order_id: str


class OrderFulfillmentSaga(Saga[SagaState]):
    listens_to = [OrderPlaced, StockReserved]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(OrderPlaced, send=lambda e: ReserveStock(order_id=e.order_id),
                step="reserving")
        self.on(StockReserved, send=lambda e: ConfirmOrder(order_id=e.order_id),
                step="confirming")


registry.register_saga(OrderFulfillmentSaga)
```

Each saga's `listens_to` declaration determines which events it receives. See [Define a Saga](define-saga.md) for creating saga classes.

## 3. Register the command handlers

The handlers run inside the `MessageBus`; the events their aggregates raise are published after the commit.

```python
from uuid import UUID, uuid4

from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.testing import FakeRepository, FakeUnitOfWork


class Reservation(AggregateRoot[UUID]):
    order_id: str = ""


reservations = FakeRepository[Reservation, UUID]()
confirmed: list[str] = []


async def reserve_stock(command: ReserveStock, uow: FakeUnitOfWork) -> EmptyCommandResult:
    reservation = Reservation(id=uuid4(), order_id=command.order_id)
    reservation._add_event(StockReserved(order_id=command.order_id))
    await reservations.save(reservation)
    return EmptyCommandResult()


async def confirm_order(command: ConfirmOrder, uow: FakeUnitOfWork) -> EmptyCommandResult:
    confirmed.append(command.order_id)
    return EmptyCommandResult()


message_bus.register_command(ReserveStock, reserve_stock, lambda: FakeUnitOfWork(reservations))
message_bus.register_command(ConfirmOrder, confirm_order, lambda: FakeUnitOfWork(reservations))
```

`_add_event` stands in for an aggregate method that records its own event.

## 4. Create the manager

```python
manager = SagaManager(
    repository=repo,
    registry=registry,
    command_bus=message_bus,
)
```

Passing a `CommandBus` fails at once:

```python
from pydomain.cqrs.command_bus import CommandBus

try:
    SagaManager(repository=repo, registry=registry, command_bus=CommandBus())
except TypeError as error:
    assert "MessageBus" in str(error)
```

## 5. Bind to the event bus

```python
manager.bind_to(message_bus)
```

`bind_to()` reads all event types from the registry and calls `message_bus.register_event(event_type, manager.handle)` for each, so every event of a registered type, including the ones the saga's own commands raise, is routed to the manager.

```python
await message_bus.dispatch(OrderPlaced(order_id="order-1", correlation_id=uuid4()))
assert confirmed == ["order-1"]
```

## 6. Schedule background recovery tasks

For production, run `recover_pending_sagas()` and `process_timeouts()` on a timer:

```python
import asyncio
import logging

logger = logging.getLogger("myapp.sagas")


async def maintenance_pass(manager: SagaManager) -> list[UUID]:
    not_recovered = await manager.recover_pending_sagas(limit=50)
    if not_recovered:
        logger.warning("%d sagas not recovered this pass", len(not_recovered))
    await manager.process_timeouts(limit=50)
    return not_recovered


async def maintenance_loop(manager: SagaManager, interval: int = 60) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await maintenance_pass(manager)
        except Exception:
            logger.exception("Saga maintenance pass failed")
```

`recover_pending_sagas()` does not stop at a saga whose recovery fails: the manager logs that saga at ERROR (its id and the exception's type) and carries on, then returns the ids of the sagas it did not recover. So `process_timeouts()` runs on every pass. The `try` only guards against infrastructure errors, such as a repository query that fails.

<!-- docs-test: check
assert await maintenance_pass(manager) == []
-->

## 7. Full bootstrap example

```python
from pydomain.cqrs.saga import SagaRepository


def bootstrap_sagas(
    repo: SagaRepository,
    registry: SagaRegistry,
    message_bus: MessageBus,
) -> SagaManager:
    manager = SagaManager(
        repository=repo,
        registry=registry,
        command_bus=message_bus,
    )
    manager.bind_to(message_bus)
    return manager
```

Start `maintenance_loop(manager)` as a background task in your application's startup, and cancel it at shutdown.

<!-- docs-test: check
assert isinstance(bootstrap_sagas(FakeSagaRepository(), SagaRegistry(), MessageBus()), SagaManager)
-->

## 8. Test the wiring

```python
import pytest

from pydomain.cqrs.saga import SagaStatus


class RecordingDispatcher:
    """A test dispatcher: records commands and publishes nothing."""

    def __init__(self) -> None:
        self.sent: list[Command] = []

    async def dispatch(self, command: Command) -> None:
        self.sent.append(command)


@pytest.mark.anyio
async def test_saga_manager_wiring() -> None:
    repo = FakeSagaRepository()
    registry = SagaRegistry()
    registry.register_saga(OrderFulfillmentSaga)
    dispatcher = RecordingDispatcher()
    manager = SagaManager(repo, registry, dispatcher)

    event = OrderPlaced(order_id="order-2", correlation_id=uuid4())
    await manager.handle(event)

    state = await repo.find_by_correlation_id(event.correlation_id, "OrderFulfillmentSaga")
    assert state is not None
    assert state.status == SagaStatus.RUNNING
    assert [type(c).__name__ for c in dispatcher.sent] == ["ReserveStock"]
```

A recording dispatcher like this one is enough to test a saga's commands; use a `MessageBus` with real handlers to test the whole flow.

<!-- docs-test: check
await test_saga_manager_wiring()
-->

## Expected outcome

A fully wired `SagaManager` that routes events to the correct sagas, persists state, dispatches commands through a bus that publishes their events, and recovers from crashes.

## Next steps

- [Handle Saga Errors](saga-error-handling.md) — configure retry and recovery
- [Prune Saga History](saga-pruning.md) — cap unbounded growth
- [Suspend, Resume & Timeout](saga-suspend-resume-timeout.md) — human-in-the-loop
