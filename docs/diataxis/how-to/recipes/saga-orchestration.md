# Recipe: Saga Orchestration

> **Adoption Level:** 5 · Prerequisites: [Saga concept](../../concepts/sagas/saga.md), [Define a Saga how-to](../sagas/define-saga.md), [Configure a Saga Manager how-to](../sagas/configure-saga-manager.md), [Saga State concept](../../concepts/sagas/saga-state.md), [Declarative vs Imperative concept](../../concepts/sagas/declarative-vs-imperative.md)

This recipe shows a complete saga-based business process — event-driven choreography with a `SagaManager` orchestrating the lifecycle (load → handle → save → dispatch). You'll build an order fulfillment saga that reserves inventory, ships items, and compensates on failure.

## What You'll Build

An **Order Fulfillment Saga** with:

- **Events:** `OrderCreated`, `ItemsReserved`, `ItemsShipped`
- **Commands:** `ReserveItems`, `ShipItems`, `NotifyCustomer` (forward) + `CancelReservation`, `CancelShipment` (compensation)
- **Saga:** `OrderFulfillmentSaga` — declarative command-mapper style with `listens_to`
- **Infrastructure:** `FakeSagaRepository`, `FakeCommandBus`, `SagaRegistry`, `SagaManager`
- **Compensation:** LIFO rollback on failure via `compensate` parameter

## Step 1: Domain Events

Every event carries a `correlation_id` so the `SagaManager` can route it to the correct saga instance.

```python
# domain/events.py
from uuid import UUID
from pydomain.ddd.domain_event import DomainEvent


class OrderCreated(DomainEvent):
    order_id: UUID
    customer_id: UUID
    correlation_id: UUID


class ItemsReserved(DomainEvent):
    order_id: UUID
    reservation_id: UUID
    correlation_id: UUID


class ItemsShipped(DomainEvent):
    order_id: UUID
    tracking_number: str
    correlation_id: UUID
```

## Step 2: Commands (Forward + Compensation)

```python
# application/commands.py
from uuid import UUID
from pydomain.cqrs.commands import Command


class ReserveItems(Command[UUID]):
    order_id: UUID


class ShipItems(Command[UUID]):
    order_id: UUID


class NotifyCustomer(Command[UUID]):
    order_id: UUID
    tracking_number: str


# Compensation commands
class CancelReservation(Command[UUID]):
    order_id: UUID


class CancelShipment(Command[UUID]):
    order_id: UUID
```

## Step 3: The Saga (Declarative Style)

```python
# domain/order_fulfillment_saga.py
from datetime import timedelta
from pydomain.cqrs.saga import Saga
from pydomain.cqrs.saga.state import SagaState

from domain.events import OrderCreated, ItemsReserved, ItemsShipped
from application.commands import (
    ReserveItems, ShipItems, NotifyCustomer,
    CancelReservation, CancelShipment,
)


class OrderFulfillmentSaga(Saga[SagaState]):
    listens_to = [OrderCreated, ItemsReserved, ItemsShipped]
    default_timeout = timedelta(hours=24)

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)

        self.on(OrderCreated,
                send=lambda e: ReserveItems(order_id=e.order_id),
                step="reserving",
                compensate=lambda e: CancelReservation(order_id=e.order_id),
                compensate_description="Undo reservation")

        self.on(ItemsReserved,
                send=lambda e: ShipItems(order_id=e.order_id),
                step="shipping",
                compensate=lambda e: CancelShipment(order_id=e.order_id),
                compensate_description="Undo shipment")

        self.on(ItemsShipped,
                send=lambda e: NotifyCustomer(
                    order_id=e.order_id,
                    tracking_number=e.tracking_number,
                ),
                step="completed",
                complete=True)
```

## Step 4: Imperative Alternative

For sagas requiring conditional logic, use handlers instead of `send`:

```python
# domain/order_fulfillment_saga.py (imperative)
class OrderFulfillmentSaga(Saga[SagaState]):
    listens_to = [OrderCreated, ItemsReserved, ItemsShipped]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)

        self.on(OrderCreated, handler=self.handle_order_created)
        self.on(ItemsReserved, handler=self.handle_reservation)
        self.on(ItemsShipped, handler=self.handle_shipment)

    async def handle_order_created(self, event: OrderCreated) -> None:
        self.state.current_step = "reserving"
        self.dispatch(ReserveItems(order_id=event.order_id))
        self.add_compensation(
            CancelReservation(order_id=event.order_id),
            description="Undo reservation",
        )

    async def handle_reservation(self, event: ItemsReserved) -> None:
        self.state.current_step = "shipping"
        if event.order_id.int % 2 == 0:
            # Even order IDs: expedited shipping
            self.dispatch(ExpeditedShipItems(order_id=event.order_id))
        else:
            self.dispatch(ShipItems(order_id=event.order_id))
        self.add_compensation(
            CancelShipment(order_id=event.order_id),
            description="Undo shipment",
        )

    async def handle_shipment(self, event: ItemsShipped) -> None:
        self.state.current_step = "completed"
        self.dispatch(NotifyCustomer(
            order_id=event.order_id,
            tracking_number=event.tracking_number,
        ))
        self.complete()
```

## Step 5: Wiring

```python
# infrastructure/wiring.py
from pydomain.cqrs.saga import SagaRegistry, SagaManager
from pydomain.testing.fake_saga_repository import FakeSagaRepository

from domain.order_fulfillment_saga import OrderFulfillmentSaga


# Real application: use a database-backed repository.
# Here we use the in-memory fake for the recipe.
saga_repository = FakeSagaRepository()

registry = SagaRegistry()
registry.register_saga(OrderFulfillmentSaga)

# command_bus is your application's CommandBus instance.
manager = SagaManager(
    repository=saga_repository,
    registry=registry,
    command_bus=command_bus,
)

# Auto-register with the message bus so events route to the manager.
manager.bind_to(message_bus)
```

`bind_to()` reads all event types from the registry and calls `message_bus.register_event()` for each one, piping every matching event to `manager.handle()`.

## Step 6: Test the Happy Path

```python
# tests/test_order_fulfillment_saga.py
import pytest
from uuid import uuid4
from pydomain.cqrs.saga import SagaRegistry, SagaManager, SagaStatus
from pydomain.testing.fake_saga_repository import FakeSagaRepository

from domain.events import OrderCreated, ItemsReserved, ItemsShipped
from domain.order_fulfillment_saga import OrderFulfillmentSaga
from application.commands import ReserveItems, ShipItems, NotifyCustomer


class FakeCommandBus:
    """Records dispatched commands for test assertions."""
    def __init__(self):
        self.dispatched: list = []

    async def dispatch(self, command):
        self.dispatched.append(command)


@pytest.mark.anyio
class TestOrderFulfillmentSaga:
    async def test_full_happy_path(self):
        repo = FakeSagaRepository()
        cmd_bus = FakeCommandBus()
        registry = SagaRegistry()
        registry.register_saga(OrderFulfillmentSaga)

        manager = SagaManager(
            repository=repo,
            registry=registry,
            command_bus=cmd_bus,
        )

        correlation_id = uuid4()
        order_id = uuid4()

        # Step 1: OrderCreated → RESERVING
        await manager.handle(OrderCreated(
            event_id=uuid4(),
            order_id=order_id,
            customer_id=uuid4(),
            correlation_id=correlation_id,
        ))

        state = await repo.find_by_correlation_id(
            correlation_id, "OrderFulfillmentSaga"
        )
        assert state is not None
        assert state.status == SagaStatus.RUNNING
        assert state.current_step == "reserving"
        assert isinstance(cmd_bus.dispatched[0], ReserveItems)

        # Step 2: ItemsReserved → SHIPPING
        await manager.handle(ItemsReserved(
            event_id=uuid4(),
            order_id=order_id,
            reservation_id=uuid4(),
            correlation_id=correlation_id,
        ))

        state = await repo.get_by_id(state.id)
        assert state.current_step == "shipping"
        assert isinstance(cmd_bus.dispatched[1], ShipItems)

        # Step 3: ItemsShipped → COMPLETED
        tracking = "TRACK-12345"
        await manager.handle(ItemsShipped(
            event_id=uuid4(),
            order_id=order_id,
            tracking_number=tracking,
            correlation_id=correlation_id,
        ))

        state = await repo.get_by_id(state.id)
        assert state.status == SagaStatus.COMPLETED
        assert state.current_step == "completed"
        assert isinstance(cmd_bus.dispatched[2], NotifyCustomer)
        assert cmd_bus.dispatched[2].tracking_number == tracking

        # State has step history
        assert len(state.step_history) == 3
        assert [s.step_name for s in state.step_history] == [
            "reserving", "shipping", "completed",
        ]
```

## Step 7: Test Compensation at the Saga Level

Compensation is triggered via `saga.fail(reason, compensate=True)`, which pops the LIFO compensation stack, hydrates commands, and queues them for dispatch:

```python
@pytest.mark.anyio
async def test_compensation_on_failure():
    repo = FakeSagaRepository()
    cmd_bus = FakeCommandBus()
    registry = SagaRegistry()
    registry.register_saga(OrderFulfillmentSaga)

    manager = SagaManager(
        repository=repo,
        registry=registry,
        command_bus=cmd_bus,
    )

    correlation_id = uuid4()
    order_id = uuid4()

    # Step 1: OrderCreated → reserves + pushes compensation
    await manager.handle(OrderCreated(
        event_id=uuid4(),
        order_id=order_id,
        customer_id=uuid4(),
        correlation_id=correlation_id,
    ))

    state = await repo.find_by_correlation_id(
        correlation_id, "OrderFulfillmentSaga"
    )

    # Compensation stack was populated by the compensate= parameter
    assert len(state.compensation_stack) == 1
    assert state.compensation_stack[0].command_type == "CancelReservation"

    # Step 2: Fail the saga — this triggers compensation at the saga level.
    # saga.fail(reason, compensate=True) calls execute_compensations(),
    # which hydrates commands from the compensation stack (LIFO) and
    # queues them via dispatch().  The manager picks them up via
    # collect_commands() and dispatches through the command bus.
    saga = OrderFulfillmentSaga(state)
    await saga.fail("Inventory service unavailable", compensate=True)

    # Saga transitioned to COMPENSATING
    assert state.status == SagaStatus.COMPENSATING

    # Compensation command is queued for dispatch
    compensation_commands = saga.collect_commands()
    assert len(compensation_commands) == 1
    assert isinstance(compensation_commands[0], CancelReservation)

    # Compensation stack is drained (LIFO pop)
    assert len(state.compensation_stack) == 0

    # The SagaManager dispatches these commands through the command bus
    # and transitions to COMPENSATED or FAILED based on results.
    # In a full integration test, this happens inside manager.handle().
```

To test compensation end-to-end (through the `SagaManager`), use `fail=True` in the `on()` registration for a terminal error event, then send that event through `manager.handle()`. The manager automatically dispatches compensation commands and transitions the state to COMPENSATED or FAILED.

## Step 8: Idempotency

The saga's `handle()` method skips already-processed events. Re-delivering the same event is a no-op:

```python
# First delivery
await manager.handle(event)

# Re-delivery (network retry, at-least-once broker)
await manager.handle(event)  # Skipped — event_id already in processed_event_ids

state = await repo.find_by_correlation_id(correlation_id, "OrderFulfillmentSaga")
assert len(state.step_history) == 1  # Only recorded once
```

Terminal sagas (COMPLETED, FAILED, COMPENSATED) ignore all further events.

## Architecture Recap

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  SagaManager                                                │
│    │                                                        │
│    ├── SagaRegistry (event → saga type lookup)              │
│    ├── SagaRepository (state persistence)                   │
│    └── CommandBus (dispatch forward + compensation cmds)    │
│                                                             │
│  Per-event flow:                                            │
│    Event → registry.get_sagas_for_event()                   │
│          → repo.find_by_correlation_id()                    │
│          → saga.handle(event)                               │
│          → repo.save(state)                                 │
│          → command_bus.dispatch(queued_commands)            │
│                                                             │
│  If COMPENSATING: dispatch compensations before forward.     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## What's Next?

- [Suspend/Resume recipe](saga-example-suspend-resume.md) — human-in-the-loop sagas
- [Handle Saga Errors how-to](../sagas/saga-error-handling.md) — retry and recovery
- [Prune Saga History how-to](../sagas/saga-pruning.md) — cap unbounded growth
- [All Modules Integration recipe](all-modules.md) — saga with full DDD + CQRS + ES stack
