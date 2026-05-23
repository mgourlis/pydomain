# Application Bootstrap

> **Adoption Level:** 3 — CQRS with Events
> **Module:** `pydomain.infrastructure.bootstrap`

## What is Application Bootstrap?

**Application Bootstrap** is the dependency-injection composition root. It assembles the `MessageBus`, `EventRegistry`, `EventStore`, and `MessageBroker` into a wired `Application` instance — ready to dispatch commands and queries.

If the `MessageBus` is the car engine, the bootstrap is the factory floor that puts the car together.

## Why It Exists

Without a bootstrap, wiring a CQRS application requires manual assembly:

```python
# Manual wiring (error-prone, repetitive)
bus = MessageBus(CommandBus(), QueryBus())
registry = EventRegistry()
for event_type in [OrderPlaced, OrderShipped, ...]:
    registry.register(event_type)
bus.register_command(PlaceOrder, PlaceOrderHandler(repo), lambda: OrderUoW(...))
bus.register_query(GetOrder, GetOrderHandler(read_store))
# ... 50 more lines
```

The bootstrap centralizes this in one place:

```python
# Bootstrap (declarative, one call)
app = await bootstrap(event_store=store, message_broker=broker)
```

It also enforces that wiring happens *once*, at startup, rather than lazily or repeatedly.

## The `Application` Class

```python
from pydomain.infrastructure.bootstrap import Application


class Application:
    def __init__(
        self,
        message_bus: MessageBus,
        event_registry: EventRegistry | None = None,
        snapshot_store: SnapshotStore | None = None,
    ) -> None: ...

    @property
    def snapshot_store(self) -> SnapshotStore | None: ...

    async def dispatch(self, message: Command[Any] | Query[Any]) -> Any: ...
```

`Application` is the thin entry point. Its `dispatch()` method delegates to the `MessageBus` — it adds no extra behavior beyond holding the optional `EventRegistry` and `SnapshotStore` for serialization and snapshot access.

## The `bootstrap()` Function

```python
from pydomain.infrastructure.bootstrap import bootstrap


async def bootstrap(
    event_store: EventStore | None = None,
    snapshot_store: SnapshotStore | None = None,
    message_bus: MessageBus | None = None,
    message_broker: MessageBroker | None = None,
    event_registry: EventRegistry | None = None,
) -> Application: ...
```

Every parameter is optional. If omitted, sensible defaults are created:

| Parameter | Default (when `None`) | Purpose |
|-----------|----------------------|---------|
| `message_bus` | `MessageBus(CommandBus(), QueryBus())` | Central dispatch |
| `event_registry` | `EventRegistry()` | Event type registration for serialization |
| `event_store` | `None` | Passed through to the `Application` for ES |
| `snapshot_store` | `None` | Passed through to the `Application` for ES |
| `message_broker` | `None` | If provided, `start()` is called during bootstrap |

If you pass a `message_broker`, `bootstrap()` calls `await broker.start()` before returning. This connects to the message broker during startup rather than lazily at first publish.

## Bootstrap Sequence

```
bootstrap()
  ├── 1. Create defaults for omitted arguments
  │      MessageBus(CommandBus(), QueryBus())
  │      EventRegistry()
  ├── 2. Wire event registry for serialization
  ├── 3. If message_broker is provided:
  │      await message_broker.start()
  ├── 4. Return Application(message_bus, event_registry, snapshot_store)
```

## Handler Registration Happens After Bootstrap

The `bootstrap()` function creates the *plumbing*, not the *wiring*. You register your application's handlers after bootstrap returns:

```python
app = await bootstrap(event_store=store, message_broker=broker)

# Register command handlers
app.message_bus.register_command(PlaceOrder, PlaceOrderHandler(...), uow_factory)
app.message_bus.register_command(CancelOrder, CancelOrderHandler(...), uow_factory)

# Register query handlers
app.message_bus.register_query(GetOrder, GetOrderHandler(read_store))

# Register event handlers
app.message_bus.register_event(OrderPlaced, SendConfirmationHandler(email))
app.message_bus.register_event(OrderPlaced, UpdateProjectionHandler(projection))
```

This separation means `bootstrap()` doesn't need to know anything about your domain — it handles infrastructure concerns, and your composition root handles domain wiring.

### Inbound Messaging Wiring

The `MessageSubscriber` and `InboundEventGateway` are also wired after bootstrap:

```python
# Wire inbound event gateway
gateway = InboundEventGateway(subscriber, app.message_bus)
gateway.register_translation(
    topic="orders",
    integration_class=OrderPlacedIntegration,
    translator=lambda e: OrderPlaced(
        order_id=UUID(e.order_id),
        customer_id=UUID(e.customer_id),
    ),
)
await subscriber.start()
```

The subscriber is not part of `bootstrap()` — like handler registration, it's the application composition root's responsibility to wire the inbound side to match the domain's event types.

## Typical Main Entry Point

```python
from pydomain.infrastructure.bootstrap import bootstrap
from pydomain.infrastructure.event_registry import EventRegistry


async def main() -> None:
    # 1. Bootstrap infrastructure
    app = await bootstrap(
        event_store=create_event_store(),
        snapshot_store=create_snapshot_store(),
        message_broker=create_broker(),
        event_registry=EventRegistry(),
    )

    # 2. Register your event types for serialization
    app.event_registry.register(OrderPlaced)
    app.event_registry.register(OrderShipped)
    app.event_registry.register(OrderCancelled)

    # 3. Wire your handlers
    uow_factory = lambda: OrderUoW(session_factory)
    app.message_bus.register_command(PlaceOrder, PlaceOrderHandler(pricing), uow_factory)
    app.message_bus.register_query(GetOrders, GetOrdersHandler(read_store))

    # 4. Dispatch
    result, events = await app.dispatch(PlaceOrder(customer_id="c1", items=[...]))
```

## Design Decision

The bootstrap is deliberately minimal. It doesn't do auto-discovery, convention-based scanning, or decorator registration. Every handler must be explicitly registered. This makes the composition root the single source of truth for what the application does — you can read the wiring to understand the application's capabilities.

## Next Steps

- **[Bootstrap an Application →](../../how-to/infrastructure/bootstrap-application.md)** — step-by-step wiring
- **[Register Handlers →](../../how-to/infrastructure/register-handlers.md)** — handler registration patterns
- **[Event Registry →](event-registry.md)** — event type registration for serialization
- **[Message Broker →](message-broker.md)** — cross-boundary messaging (outbound)
- **[MessageSubscriber →](message-subscriber.md)** — receiving integration events (inbound)
- **[InboundEventGateway →](inbound-event-gateway.md)** — bridging external brokers to the internal bus
- **[Message Bus →](message-bus.md)** — the central dispatch mechanism
