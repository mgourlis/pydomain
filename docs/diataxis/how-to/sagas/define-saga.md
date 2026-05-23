# How to Define a Saga

> **Adoption Level:** 5 · Prerequisites: [Saga concept](../../concepts/sagas/saga.md), [Saga State concept](../../concepts/sagas/saga-state.md), [Declarative vs Imperative](../../concepts/sagas/declarative-vs-imperative.md)

This guide walks through creating a saga from scratch — declaring events, choosing a style, and testing.

## 1. Define the domain events (if needed)

```python
from uuid import UUID
from pydomain.ddd.domain_event import DomainEvent


class OrderCreated(DomainEvent):
    order_id: UUID
    customer_id: UUID
    correlation_id: UUID


class PaymentConfirmed(DomainEvent):
    order_id: UUID
    amount: float
    correlation_id: UUID


class ItemsShipped(DomainEvent):
    order_id: UUID
    tracking_number: str
    correlation_id: UUID
```

Every event must carry a `correlation_id` so the `SagaManager` can route it to the correct saga instance.

## 2. Define the commands

```python
from pydomain.cqrs.commands import Command


class ReserveItems(Command[UUID]):
    order_id: UUID


class CancelReservation(Command[UUID]):
    order_id: UUID


class ShipItems(Command[UUID]):
    order_id: UUID


class NotifyCustomer(Command[UUID]):
    order_id: UUID
    tracking_number: str
```

Compensation commands (like `CancelReservation`) follow the same `Command` pattern as forward commands.

## 3. Option A: Declarative saga (command-mapper)

For straight-line event-to-command mappings:

```python
from datetime import timedelta
from pydomain.cqrs.saga import Saga
from pydomain.cqrs.saga.state import SagaState


class OrderFulfillmentSaga(Saga[SagaState]):
    listens_to = [OrderCreated, PaymentConfirmed, ItemsShipped]
    default_timeout = timedelta(hours=24)

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)

        self.on(OrderCreated,
                send=lambda e: ReserveItems(order_id=e.order_id),
                step="reserving",
                compensate=lambda e: CancelReservation(order_id=e.order_id))

        self.on(PaymentConfirmed,
                send=lambda e: ShipItems(order_id=e.order_id),
                step="shipping",
                compensate=lambda e: CancelShipment(order_id=e.order_id))

        self.on(ItemsShipped,
                send=lambda e: NotifyCustomer(order_id=e.order_id, tracking_number=e.tracking_number),
                step="completed",
                complete=True)
```

## 3. Option B: Imperative saga (handler)

For conditional logic or multi-branch dispatch:

```python
class OrderFulfillmentSaga(Saga[SagaState]):
    listens_to = [OrderCreated, PaymentConfirmed, ItemsShipped]
    default_timeout = timedelta(hours=24)

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)

        self.on(OrderCreated, handler=self.handle_order_created)
        self.on(PaymentConfirmed, handler=self.handle_payment)
        self.on(ItemsShipped, handler=self.handle_shipment)

    async def handle_order_created(self, event: OrderCreated) -> None:
        self.state.current_step = "reserving"
        self.dispatch(ReserveItems(order_id=event.order_id))
        self.add_compensation(
            CancelReservation(order_id=event.order_id),
            description="Undo reservation"
        )

    async def handle_payment(self, event: PaymentConfirmed) -> None:
        self.state.current_step = "shipping"
        if event.amount > 1000:
            # High-value order — require manual approval
            self.dispatch(RequestApproval(order_id=event.order_id))
            self.suspend(
                reason=f"High-value order {event.order_id} requires approval",
                timeout=timedelta(hours=48),
            )
        else:
            self.dispatch(ShipItems(order_id=event.order_id))

    async def handle_shipment(self, event: ItemsShipped) -> None:
        self.state.current_step = "completed"
        self.complete()
```

## 4. Register the saga

```python
from pydomain.cqrs.saga import SagaRegistry

registry = SagaRegistry()
registry.register_saga(OrderFulfillmentSaga)
```

## 5. Wire up the manager

```python
from pydomain.cqrs.saga import SagaManager

manager = SagaManager(
    repository=saga_repository,
    registry=registry,
    command_bus=command_bus,
)

# Auto-register with the event bus
manager.bind_to(message_bus)
```

See [Configure a Saga Manager](configure-saga-manager.md) for the full wiring.

## 6. Test the saga

```python
import pytest
from uuid import uuid4
from pydomain.testing.saga import FakeSagaRepository


@pytest.mark.anyio
async def test_order_fulfillment_saga_flow():
    repo = FakeSagaRepository()
    registry = SagaRegistry()
    registry.register_saga(OrderFulfillmentSaga)

    from pydomain.testing.command_bus import FakeCommandBus
    cmd_bus = FakeCommandBus()

    manager = SagaManager(repository=repo, registry=registry, command_bus=cmd_bus)

    correlation_id = uuid4()

    # Start the saga with an OrderCreated event
    event = OrderCreated(
        event_id=uuid4(),
        order_id=uuid4(),
        customer_id=uuid4(),
        correlation_id=correlation_id,
    )
    await manager.handle(event)

    # Assert state
    state = await repo.find_by_correlation_id(correlation_id, "OrderFulfillmentSaga")
    assert state is not None
    assert state.current_step == "reserving"
    assert state.status == SagaStatus.RUNNING

    # Assert a command was dispatched
    dispatched = cmd_bus.dispatched_commands()
    assert len(dispatched) == 1
    assert isinstance(dispatched[0], ReserveItems)
```

## Expected outcome

A saga class that reacts to events, dispatches commands, compensates on failure, and can be tested with in-memory fakes.

## Next steps

- [Configure a Saga Manager](configure-saga-manager.md) — full application wiring
- [Implement Compensation](saga-compensation.md) — add rollback logic
- [Suspend, Resume & Timeout](saga-suspend-resume-timeout.md) — add human-in-the-loop steps
