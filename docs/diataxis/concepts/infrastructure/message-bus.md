# Message Bus

> **Adoption Level:** 3 — CQRS with Events
> **Module:** `pydomain.cqrs.message_bus` (conceptual — the Command Bus and Query Bus together form the message bus)

## What is the Message Bus?

The **Message Bus** is the central dispatch mechanism for all message types — commands, queries, and events. It's not a single class but a pattern embodied by the `CommandBus` for write operations, the `QueryBus` for read operations, and registered event handlers for domain event reactions. A unified `dispatch()` method routes commands to the `CommandBus`, queries to the `QueryBus`, and domain events directly to registered handlers.

## Message Flow

```
                   ┌─────────────┐
                   │  MessageBus  │
                   └──────┬──────┘
                          │
          ┌───────────────┼──────────────────┐
          │               │                  │
     CommandBus       QueryBus      Event Handlers
          │               │                  │
     Commands         Queries       Domain Events
     (modify)         (read)        (react / dispatch)

     Domain events arrive via two paths:
     ① Collected after command commit (side-effect)
     ② Direct dispatch via bus.dispatch(event)
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

Domain events are dispatched to registered handlers through two paths:

**① Post-commit dispatch** — After a successful command commit, the bus publishes each collected event to all matching handlers, catching and logging per-handler failures independently:

```
OrderPlaced event
  ├── Handler A: SendConfirmationEmail  ← fails (logged, not propagated)
  ├── Handler B: UpdateInventory         ← succeeds
  └── Handler C: PublishIntegrationEvent ← succeeds
```

**② Direct dispatch** — Externally-originated domain events (from the `InboundEventGateway`) enter through the same unified `dispatch()` method:

```python
await message_bus.dispatch(event)  # event: DomainEvent instance
```

Direct dispatch bypasses the UoW and pipeline behaviors — the event already represents committed state. It's routed straight to registered event handlers with the same per-handler failure isolation.

## Orchestration from Event Handlers

Event handlers can dispatch new commands by injecting the `CommandBus`:

```python
class StartOnboardingHandler:
    def __init__(self, bus: CommandBus) -> None:
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
| Command | `CommandBus` | `register(command_type, handler, uow_factory, behaviors)` | `(command, uow) → result` |
| Query | `QueryBus` | `register(query_type, handler, behaviors)` | `(query) → result` |
| Event | `MessageBus` | `register_event(event_type, handler)` | `(event) → None` |

## Unified Dispatch

`MessageBus.dispatch()` accepts all three message types and routes accordingly:

```python
async def dispatch(
    self, message: Command[Any] | Query[Any] | DomainEvent
) -> Any:
```

| Message Type | Route | UoW | Pipeline | Returns |
|-------------|-------|-----|----------|---------|
| `Command` | → `CommandBus` | Yes | Yes | `CommandResult` |
| `Query` | → `QueryBus` | No | Yes | `QueryResult` |
| `DomainEvent` | → Event Handlers | No | No | `None` |

Domain events skip both the UoW and pipeline behaviors — they represent already-committed state, whether emitted internally after a command commit or arriving from an external broker through the `InboundEventGateway`.

## Pipeline Behaviors on Both Sides

Both the Command Bus and Query Bus support pipeline behaviors:

```python
# Command pipeline
bus.register(
    command_type=PlaceOrder,
    handler=handler,
    uow_factory=uow_factory,
    behaviors=[LoggingBehavior(), ValidationBehavior(), IdempotencyBehavior()],
)

# Query pipeline
query_bus.register(
    query_type=GetOrder,
    handler=handler,
    behaviors=[LoggingBehavior()],
)
```

Behaviors apply to commands and queries independently — you can add logging to all messages, validation only to commands, etc.

## Dependency Injection Pattern

Handlers receive their dependencies through `__init__`, not through a service locator:

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
