# How to Handle Domain Events

> **Prerequisites:** [Handlers concept](../../concepts/cqrs/handlers.md), [Domain Events concept](../../concepts/ddd/domain-events.md)

## Problem

You need to react to a domain event — send an email, update a projection, dispatch a new command — after a successful command commit. Or you need to handle a domain event that arrived from an external system through an inbound message broker.

## Solution

Implement an `EventHandler` class and register it with the Message Bus. Event handlers are fire-and-forget — they return `None` and fail independently. A failure after a command is logged and swallowed; a failure of an inbound event is NACKed, so the broker redelivers it.

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

**Post-commit (internal):** After a command succeeds, the unit of work has collected and stamped its events, and the `MessageBus` dispatches them:

```
1. Command handler calls aggregate method
2. Aggregate records event via self._add_event(...)
3. The bus commits right after the handler, inside the pipeline → `uow.commit()` collects and stamps the events
4. MessageBus.dispatch() → registered event handlers
5. Handlers execute independently — failure in one doesn't affect others, and the command's result is returned
```

In-process dispatch is best-effort. A command that committed and then failed or was cancelled (a cancellation during its commit waits for the commit, then propagates) does not return its events, so step 4 never runs for them, and an idempotent retry returns the cached result without them. Handlers that must see every committed change subscribe through the outbox path instead: the outbound relay and the inbound gateway, or a catch-up subscription, which also survive a crash.

**Direct dispatch (external):** Events arriving from external brokers enter through the `InboundEventGateway` and are dispatched directly via `MessageBus.dispatch(domain_event)`, bypassing the UoW — the event already represents committed state elsewhere. Handlers run with the same per-handler failure isolation; once every handler has run, the dispatch raises `EventHandlersFailed` if any failed, so the subscriber NACKs the message.

## Independent Failure

The Message Bus catches and logs per-handler exceptions. If one handler fails, others still run:

```text
OrderPlaced dispatched to 3 handlers:
  ├── SendOrderConfirmationHandler  ← fails (logged)
  ├── UpdateInventoryHandler        ← succeeds
  └── PublishIntegrationEventHandler ← succeeds
```

What happens next depends on the path ([ADR-081](../../../adr/ADR-081-handler-failures-reach-the-inbound-gateway.md)):

- **After a command**, the failure is not raised. The command result is returned normally — event handler failures are side effects that don't roll back the transaction.
- **On a direct dispatch**, `MessageBus.dispatch(event)` raises `EventHandlersFailed` once every handler has run. Its `exceptions` are the failed handlers' own exceptions. Through the `InboundEventGateway` the subscriber then NACKs the message, and the broker redelivers it to **every** handler of the event, including the ones that succeeded. Make inbound handlers idempotent with `EventIdempotencyBehavior` (next section), so that only the failed handler runs again.

A command handler that dispatches a `DomainEvent` directly through `MessageBus.dispatch(event)` gets `EventHandlersFailed` too, and its command fails and rolls back. Let the command's aggregate record the event instead, so the bus publishes it after the commit.

## Skip Duplicate Deliveries

To skip an event a handler has already handled, wrap the handler in an `EventIdempotencyBehavior`. Build one instance per registration and give each a stable `consumer` name; handlers may share one store:

```python
from pydomain.cqrs.behaviors import EventIdempotencyBehavior

message_bus.register_event(
    OrderPlaced,
    SendOrderConfirmationHandler(email),
    behaviors=[EventIdempotencyBehavior(store, consumer="order-confirmation-email")],
)
message_bus.register_event(
    OrderPlaced,
    UpdateInventoryHandler(inventory),
    behaviors=[EventIdempotencyBehavior(store, consumer="inventory-projection")],
)
```

- The key is the event's `event_id` and the `consumer`, so each event and each handler has its own. An event that arrives again with the same `event_id` (a broker's redelivery through the `InboundEventGateway`, or one event object dispatched twice) is skipped and logged at INFO.
- The event is marked after its handler succeeds, so a handler that raised runs again on the next delivery. Through the `InboundEventGateway` the failure is NACKed, so the broker redelivers the message, and only the handlers that have not succeeded run.
- With a store that also implements `MessageReservationStore`, a delivery that arrives while the first one still runs gets `EventInProgressError`, which the bus logs at ERROR like any handler failure and the gateway NACKs, so the broker redelivers it later.
- Renaming a `consumer` changes its keys, so events handled before the rename run again. Without `consumer`, the behavior uses the handler's `module:qualname` and warns that 0.5.0 requires the name. Reusing one instance for a second handler raises `EventIdempotencyMisuseError`.

[Idempotency & Locking](../../concepts/cqrs/idempotency-and-locking.md#event-idempotency) explains both store flows and their limits.

## Orchestration: Dispatching Commands from Handlers

Event handlers can dispatch new commands by injecting the `MessageBus`. Inject the `MessageBus`, not the `CommandBus`: a command sent through the `MessageBus` has its own events published, while one sent through a bare `CommandBus` returns them to the caller and nothing publishes them.

```python
class StartOnboardingHandler:
    def __init__(self, bus: MessageBus) -> None:
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
    def __init__(self, bus: MessageBus) -> None:
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
