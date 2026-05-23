# How to Configure the Command Bus

> **Prerequisites:** [Command Bus concept](../../concepts/cqrs/command-bus.md), [Unit of Work concept](../../concepts/cqrs/unit-of-work.md)

## Problem

You need to wire up the Command Bus — register handlers with their UoW factories and pipeline behaviors — so that command dispatch works end-to-end.

## Solution

Create a `CommandBus` instance. Register each command type with its handler, a UoW factory, and optional behaviors.

## Steps

### 1. Create the bus

```python
from pydomain.cqrs.command_bus import CommandBus

bus = CommandBus()
```

### 2. Define a UoW factory

```python
def create_order_uow() -> OrderUoW:
    session = session_factory()
    uow = OrderUoW(session)
    return uow
```

The factory must create a **fresh** UoW per call — the bus calls it for every `dispatch()`.

### 3. Register handlers

```python
bus.register(
    command_type=PlaceOrder,
    handler=PlaceOrderHandler(pricing_service),
    uow_factory=create_order_uow,
)

bus.register(
    command_type=CancelOrder,
    handler=CancelOrderHandler(refund_service),
    uow_factory=create_order_uow,
)
```

### 4. Dispatch commands

```python
result, events = await bus.dispatch(
    PlaceOrder(
        customer_id=customer_id,
        items=[OrderLine(product_id=p1, quantity=2)],
    )
)

print(result.order_id)  # PlaceOrderResult — typed
print(len(events))      # Domain events produced
```

## Adding Pipeline Behaviors

Pass behaviors at registration time:

```python
from pydomain.cqrs.behaviors import (
    LoggingBehavior,
    ValidationBehavior,
    IdempotencyBehavior,
    AggregateLockingBehavior,
)

bus.register(
    command_type=PlaceOrder,
    handler=PlaceOrderHandler(pricing_service),
    uow_factory=create_order_uow,
    behaviors=[
        LoggingBehavior(),
        ValidationBehavior(validators={
            PlaceOrder: [validate_items_not_empty],
        }),
        IdempotencyBehavior(store=processed_command_store),
        AggregateLockingBehavior(
            provider=redis_lock_provider,
            resolver=lock_key_resolver,
        ),
    ],
)
```

Behaviors run in registration order: logging first, then validation, then idempotency, then locking, then the handler.

## Multiple Command Types, Same Handler Pattern

When multiple commands share the same UoW type, extract the registration into a function:

```python
def register_order_command(
    bus: CommandBus,
    command_type: type,
    handler: object,
    behaviors: list | None = None,
) -> None:
    bus.register(
        command_type=command_type,
        handler=handler,
        uow_factory=create_order_uow,
        behaviors=behaviors or [LoggingBehavior()],
    )


register_order_command(bus, PlaceOrder, PlaceOrderHandler(pricing_service))
register_order_command(bus, CancelOrder, CancelOrderHandler(refund_service))
register_order_command(bus, AddItem, AddItemHandler())
```

## Bootstrap Function

For production, extract all wiring into a bootstrap function:

```python
def bootstrap_command_bus(
    session_factory: Callable[[], Session],
    pricing: PricingService,
    refund: RefundService,
    lock_provider: LockProvider,
    processed_store: ProcessedCommandStore,
) -> CommandBus:
    bus = CommandBus()

    def order_uow_factory() -> OrderUoW:
        return OrderUoW(session_factory())

    behaviors = [
        LoggingBehavior(),
        IdempotencyBehavior(processed_store),
        AggregateLockingBehavior(lock_provider, DictLockKeyResolver()),
    ]

    bus.register(PlaceOrder, PlaceOrderHandler(pricing), order_uow_factory, behaviors)
    bus.register(CancelOrder, CancelOrderHandler(refund), order_uow_factory, behaviors)

    return bus
```

## Verification

Test the wiring with a [Fake Unit of Work](../../how-to/testing/use-fake-uow.md):

```python
from pydomain.testing.fake_uow import FakeUnitOfWork


async def test_place_order_dispatch():
    uow = FakeUnitOfWork()
    bus = CommandBus()
    bus.register(
        command_type=PlaceOrder,
        handler=PlaceOrderHandler(fake_pricing),
        uow_factory=lambda: uow,
    )

    result, events = await bus.dispatch(PlaceOrder(customer_id=..., items=[...]))

    assert result.status == "placed"
    assert len(events) == 1
    assert isinstance(events[0], OrderPlaced)
```

## See Also

- [Command Bus concept](../../concepts/cqrs/command-bus.md)
- [Unit of Work concept](../../concepts/cqrs/unit-of-work.md)
- [Add a Pipeline Behavior](add-pipeline-behavior.md)
- [Configure the Query Bus](configure-query-bus.md)
- [Bootstrap the Application](../infrastructure/bootstrap-application.md)
