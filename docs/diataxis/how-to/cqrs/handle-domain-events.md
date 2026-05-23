# How to Handle Domain Events

> **Prerequisites:** [Handlers concept](../../concepts/cqrs/handlers.md), [Domain Events concept](../../concepts/ddd/domain-events.md)

## Problem

You need to react to a domain event — send an email, update a projection, dispatch a new command — after a successful command commit. Or you need to handle a domain event that arrived from an external system through an inbound message broker.

## Solution

Implement an `EventHandler` class and register it with the Message Bus. Event handlers are fire-and-forget — they return `None` and fail independently.

## Steps

### 1. Create the event handler

```python
from pydomain.cqrs.handlers import EventHandler
from pydomain.ddd.domain_event import DomainEvent


class SendOrderConfirmationHandler:
    def __init__(self, email_service: EmailService) -> None:
        self._email = email_service

    async def __call__(self, event: OrderPlaced) -> None:
        await self._email.send(
            to=event.customer_email,
            subject=f"Order {event.order_id} confirmed",
            body=f"Your order for {event.total_amount} has been placed.",
        )
```

### 2. Register with the message bus

```python
message_bus.register_event(OrderPlaced, SendOrderConfirmationHandler(email_service))
```

Multiple handlers can be registered for the same event type:

```python
message_bus.register_event(OrderPlaced, SendOrderConfirmationHandler(email))
message_bus.register_event(OrderPlaced, UpdateInventoryHandler(inventory))
message_bus.register_event(OrderPlaced, PublishIntegrationEventHandler(broker))
```

### 3. Events are dispatched

Events reach handlers through two paths:

**Post-commit (internal):** After a command succeeds, the CommandBus collects stamped events and dispatches them:

```
1. Command handler calls aggregate method
2. Aggregate records event via self._add_event(...)
3. CommandBus.commit() → UoW collects and stamps events
4. MessageBus.dispatch() → registered event handlers
5. Handlers execute independently — failure in one doesn't affect others
```

**Direct dispatch (external):** Events arriving from external brokers enter through the `InboundEventGateway` and are dispatched directly via `MessageBus.dispatch(domain_event)`, bypassing the UoW — the event already represents committed state elsewhere. Handlers run with the same per-handler failure isolation.

## Independent Failure

The Message Bus catches and logs per-handler exceptions. If one handler fails, others still run:

```python
OrderPlaced dispatched to 3 handlers:
  ├── SendOrderConfirmationHandler  ← fails (logged, not raised)
  ├── UpdateInventoryHandler        ← succeeds
  └── PublishIntegrationEventHandler ← succeeds
```

The command result is returned normally — event handler failures are side effects that don't roll back the transaction.

## Orchestration: Dispatching Commands from Handlers

Event handlers can dispatch new commands by injecting the Command Bus:

```python
class StartOnboardingHandler:
    def __init__(self, bus: CommandBus) -> None:
        self._bus = bus

    async def __call__(self, event: UserRegistered) -> None:
        await self._bus.dispatch(
            CreateWelcomeDiscount(user_id=event.user_id)
        )
        await self._bus.dispatch(
            SendWelcomeEmail(
                user_id=event.user_id,
                email=event.email,
            )
        )
```

This is the foundation of saga orchestration — event → handler → command → event → handler → ...

## Event Handler Patterns

### Side effects (email, SMS, notifications)

```python
class SendNotificationHandler:
    async def __call__(self, event: OrderShipped) -> None:
        await self._notifier.send(
            event.customer_id,
            f"Your order {event.order_id} has shipped via {event.carrier}",
        )
```

### Projection updates

```python
class UpdateOrderProjectionHandler:
    def __init__(self, store: OrderReadStore) -> None:
        self._store = store

    async def __call__(self, event: OrderPlaced) -> None:
        await self._store.insert({
            "order_id": event.order_id,
            "customer_id": event.customer_id,
            "total": event.total_amount,
            "status": "placed",
        })
```

### Cross-aggregate coordination

```python
class ReserveInventoryHandler:
    def __init__(self, bus: CommandBus) -> None:
        self._bus = bus

    async def __call__(self, event: OrderPlaced) -> None:
        for item in event.items:
            await self._bus.dispatch(
                ReserveStock(product_id=item.product_id, quantity=item.quantity)
            )
```

## Event Handler Dependencies

Inject dependencies via `__init__`:

```python
class OrderEventHandler:
    def __init__(
        self,
        email: EmailService,
        inventory: InventoryService,
        bus: CommandBus,
    ) -> None:
        self._email = email
        self._inventory = inventory
        self._bus = bus

    async def __call__(self, event: OrderPlaced) -> None:
        await self._email.send_confirmation(...)
        await self._inventory.reserve(...)
        await self._bus.dispatch(CreateShipment(...))
```

## See Also

- [Handlers concept](../../concepts/cqrs/handlers.md)
- [Domain Events concept](../../concepts/ddd/domain-events.md)
- [Publish a Domain Event](../ddd/publish-domain-event.md)
- [Configure the Command Bus](configure-command-bus.md)
- [InboundEventGateway concept](../../concepts/infrastructure/inbound-event-gateway.md) — receiving events from external brokers
