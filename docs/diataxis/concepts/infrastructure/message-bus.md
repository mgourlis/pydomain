# Message Bus

<!-- docs-test: runnable -->

> **Adoption Level:** 3 — CQRS with Events
> **Module:** `pydomain.infrastructure.message_bus` (`MessageBus`, a facade over the Command Bus, Query Bus and Event Bus)

## What is the Message Bus?

The **Message Bus** is the central dispatch mechanism for all message types — commands, queries, and events. The `MessageBus` class is a facade over the `CommandBus` for write operations, the `QueryBus` for read operations, and the `EventBus` for domain event dispatch. Its unified `dispatch()` method routes commands to the `CommandBus`, queries to the `QueryBus`, and domain events to the `EventBus`.

## Message Flow

```
                   ┌─────────────┐
                   │  MessageBus  │
                   └──────┬──────┘
                          │
          ┌───────────────┼─────────────────┐
          │               │                 │
     CommandBus       QueryBus          EventBus
          │               │                 │
     Commands         Queries      Domain Events
     (modify)         (read)       (react / dispatch)

     Domain events arrive via two paths:
     ① Collected after command commit → EventBus.dispatch_many()
     ② Direct dispatch → EventBus.dispatch() (via InboundEventGateway)
```

## CommandBus: The Write Path

Commands flow through the Command Bus with full transactional support:

```
dispatch(PlaceOrder(...))
  → Pipeline (behaviors → handler)
  → UnitOfWork.commit() or rollback()
  → Collect stamped events
  → Return (result, events)
```

## QueryBus: The Read Path

Queries flow through the Query Bus with no transactional overhead:

```
dispatch(GetOrder(...))
  → Pipeline (behaviors → handler)
  → Return typed result
```

## Event Dispatch

Domain events are dispatched to registered handlers through two paths. On both, every handler runs, in registration order, each through its own pipeline, and one handler's failure never stops the others. The handlers of a dispatch are the ones registered when it starts: a handler registered by another handler while the event is being dispatched runs from the next dispatch, not in this one. What happens to the failures differs ([ADR-081](../../../adr/ADR-081-handler-failures-reach-the-inbound-gateway.md) point 1).

**① Post-commit dispatch** — After a successful command commit, the bus publishes each collected event to all matching handlers, catching and logging per-handler failures independently. The command has committed, so the failures are swallowed and its result is returned:

```
OrderPlaced event
  ├── Handler A: SendConfirmationEmail  ← fails (logged, not propagated)
  ├── Handler B: UpdateInventory         ← succeeds
  └── Handler C: PublishIntegrationEvent ← succeeds
```

**② Direct dispatch** — Externally-originated domain events (from the `InboundEventGateway`) enter through the same unified `dispatch()` method:

<!-- docs-test: skip -->
```python
await message_bus.dispatch(event)  # event: DomainEvent instance
```

Direct dispatch bypasses the UoW: the event already represents committed state. It's routed straight to the registered event handlers and their behaviors. Once every handler has run, it raises `EventHandlersFailed` if any of them failed.

### Handler Failures

`EventHandlersFailed` is an `ExceptionGroup` and a `CQRSError`. Its `exceptions` are the failed handlers' own exceptions, unchanged and in registration order, and its message names only the event type and the count, `1 handler(s) failed for ShipmentDispatched`. Catch it whole with `except EventHandlersFailed`, or one member type with `except*`:

```python
from uuid import UUID, uuid4

from pydomain.cqrs import EventHandlersFailed
from pydomain.ddd import DomainEvent
from pydomain.infrastructure import MessageBus


class ShipmentDispatched(DomainEvent):
    shipment_id: UUID


class CarrierUnavailable(Exception):
    """A synthetic transient failure."""


async def notify_carrier(event: ShipmentDispatched) -> None:
    raise CarrierUnavailable


async def update_tracking(event: ShipmentDispatched) -> None:
    pass  # still runs


bus = MessageBus()
bus.register_event(ShipmentDispatched, notify_carrier)
bus.register_event(ShipmentDispatched, update_tracking)

try:
    await bus.dispatch(ShipmentDispatched(shipment_id=uuid4()))
except EventHandlersFailed as failed:
    message = failed.message  # "1 handler(s) failed for ShipmentDispatched"
    causes = [type(e) for e in failed.exceptions]  # [CarrierUnavailable]
```

<!-- docs-test: check
assert message == "1 handler(s) failed for ShipmentDispatched"
assert causes == [CarrierUnavailable]
-->

The `InboundEventGateway` lets it propagate, so the subscriber NACKs the message and the broker redelivers it ([Inbound Event Gateway](inbound-event-gateway.md#failure-modes)). A command handler that dispatches a `DomainEvent` directly fails with it as well, and its command rolls back; dispatch commands from event handlers instead, and let the bus publish the committed events.

## Orchestration from Event Handlers

Event handlers can dispatch new commands by injecting the `MessageBus`. Do not inject the `CommandBus` for this: it returns the new command's events to the caller instead of publishing them, so they are lost.

<!-- docs-test: skip -->
```python
class StartOnboardingHandler:
    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus

    async def __call__(self, event: UserRegistered) -> None:
        await self._bus.dispatch(
            CreateWelcomeDiscount(user_id=event.user_id)
        )
```

This pattern enables saga-like workflows: event → handler → command → event → handler → ...

## Registration Overview

| Message Type | Registered On | Method | Handler Signature |
|-------------|---------------|--------|-------------------|
| Command | `MessageBus` (delegates to `CommandBus`) | `register_command(command_type, handler, uow_factory, behaviors)` | `(command, uow) → result` |
| Query | `MessageBus` (delegates to `QueryBus`) | `register_query(query_type, handler, behaviors)` | `(query) → result` |
| Event | `MessageBus` (delegates to `EventBus`) | `register_event(event_type, handler, behaviors)` | `(event) → None` |

## Unified Dispatch

`MessageBus.dispatch()` accepts all three message types and routes accordingly:

<!-- docs-test: skip -->
```python
async def dispatch(
    self, message: Command[Any] | Query[Any] | DomainEvent
) -> Any: ...
```

| Message Type | Route | UoW | Pipeline | Returns |
|-------------|-------|-----|----------|---------|
| `Command` | → `CommandBus` | Yes | Yes | `CommandResult` |
| `Query` | → `QueryBus` | No | Yes | `QueryResult` |
| `DomainEvent` | → `EventBus` → Event Handlers | No | Per-handler via behaviors | `None` |

Domain events skip the UoW — they represent already-committed state, whether emitted internally after a command commit or arriving from an external broker through the `InboundEventGateway`. Pipeline behaviors configured on the event handler itself (e.g., `EventIdempotencyBehavior`) are still applied; what they skip is the bus-level behavior pipeline. `EventIdempotencyBehavior` keys an event on its `event_id` and the handler's `consumer` name, so it treats an internal and an inbound event alike ([Idempotency & Locking](../cqrs/idempotency-and-locking.md#event-idempotency)).

## Pipeline Behaviors on All Message Types

The Command Bus, Query Bus, and Event Bus all support pipeline behaviors:

<!-- docs-test: skip -->
```python
# Command pipeline
bus.register_command(
    command_type=PlaceOrder,
    handler=handler,
    uow_factory=uow_factory,
    behaviors=[LoggingBehavior(), ValidationBehavior(), IdempotencyBehavior()],
)

# Query pipeline
bus.register_query(
    query_type=GetOrder,
    handler=handler,
    behaviors=[LoggingBehavior()],
)

# Event pipeline (via MessageBus.register_event or EventBus.register)
bus.register_event(
    event_type=OrderPlaced,
    handler=email_handler,
    behaviors=[EventIdempotencyBehavior(store, consumer="order-email")],
)
```

Behaviors apply to each message type independently — you can add logging to all messages, validation only to commands, idempotency to events, etc.

## Dependency Injection Pattern

Handlers receive their dependencies through `__init__`, not through a service locator:

<!-- docs-test: skip -->
```python
# Handler with injected dependencies
class PlaceOrderHandler:
    def __init__(
        self,
        pricing: PricingService,
        inventory: InventoryService,
    ) -> None:
        self._pricing = pricing
        self._inventory = inventory

    async def __call__(self, cmd: PlaceOrder, uow: OrderUoW) -> PlaceOrderResult:
        # Use self._pricing, self._inventory
        ...
```

The bus never resolves dependencies — that's the application bootstrap layer's responsibility.

## Next Steps

- **[Bootstrap the Application →](../infrastructure/bootstrap.md)** — wiring everything together
- **[Configure the Command Bus →](../../how-to/cqrs/configure-command-bus.md)** — command bus setup
- **[Configure the Query Bus →](../../how-to/cqrs/configure-query-bus.md)** — query bus setup
- **[Handle Domain Events →](../../how-to/cqrs/handle-domain-events.md)** — event handler wiring
- **[Register Handlers →](../../how-to/infrastructure/register-handlers.md)** — handler registration
- **[MessageSubscriber Protocol →](message-subscriber.md)** — receiving integration events from external brokers
- **[InboundEventGateway →](inbound-event-gateway.md)** — bridging external brokers to the internal bus
