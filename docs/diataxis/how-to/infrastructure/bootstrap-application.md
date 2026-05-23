# How to Bootstrap the Application

> **Prerequisites:** [Application Bootstrap concept](../../concepts/infrastructure/bootstrap.md), [Message Bus concept](../../concepts/infrastructure/message-bus.md)

## Problem

You need to wire all infrastructure components — message bus, event registry, event store, message broker — into a configured `Application` ready to dispatch commands and queries.

## Solution

Call `bootstrap()` with your infrastructure dependencies, then register your handlers and event types after bootstrap returns.

## Steps

### 1. Create your infrastructure components

```python
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.infrastructure.event_registry import EventRegistry

# Create the components you need
event_store = create_event_store()          # Optional — for event sourcing
snapshot_store = create_snapshot_store()    # Optional — for snapshots
message_broker = create_broker()            # Optional — for integration events
event_registry = EventRegistry()            # Optional — defaults created if omitted
```

All parameters to `bootstrap()` are optional. Omit what you don't need.

### 2. Call bootstrap()

```python
from pydomain.infrastructure.bootstrap import bootstrap


app = await bootstrap(
    event_store=event_store,
    snapshot_store=snapshot_store,
    message_broker=message_broker,
    event_registry=event_registry,
)
```

If you omit `message_bus`, a default `MessageBus(CommandBus(), QueryBus())` is created. If you omit `event_registry`, a default `EventRegistry()` is created. If you provide a `message_broker`, its `start()` is called during bootstrap.

### 3. Register event types for serialization

```python
app.event_registry.register(OrderPlaced)
app.event_registry.register(OrderShipped)
app.event_registry.register(OrderCancelled)
```

All domain event types that will be persisted or published must be registered.

### 4. Register command handlers

```python
# UoW factory — called once per command dispatch
uow_factory = lambda: OrderUoW(session_factory)

app.message_bus.register_command(
    command_type=PlaceOrder,
    handler=PlaceOrderHandler(pricing_service, inventory_service),
    uow_factory=uow_factory,
    behaviors=[LoggingBehavior(), ValidationBehavior()],
)

app.message_bus.register_command(
    command_type=CancelOrder,
    handler=CancelOrderHandler(),
    uow_factory=uow_factory,
)
```

Each command type gets one handler, one UoW factory, and optional pipeline behaviors.

### 5. Register query handlers

```python
app.message_bus.register_query(
    query_type=GetOrder,
    handler=GetOrderHandler(read_store=order_read_store),
)

app.message_bus.register_query(
    query_type=GetCustomerOrders,
    handler=GetCustomerOrdersHandler(read_store=order_read_store),
)
```

### 6. Register event handlers

```python
app.message_bus.register_event(
    OrderPlaced,
    SendOrderConfirmationHandler(email_service),
)
app.message_bus.register_event(
    OrderPlaced,
    PublishOrderPlacedHandler(message_broker),
)
```

Multiple handlers per event type are allowed.

### 7. Dispatch

```python
# Commands — returns (result, events)
result, events = await app.dispatch(
    PlaceOrder(customer_id="c1", items=[OrderItem("widget", 2)])
)

# Queries — returns result directly
orders = await app.dispatch(
    GetCustomerOrders(customer_id="c1")
)
```

## Complete Example

```python
from pydomain.infrastructure.bootstrap import bootstrap
from pydomain.infrastructure.event_registry import EventRegistry
from pydomain.cqrs.behaviors import LoggingBehavior


async def build_app() -> Application:
    app = await bootstrap(
        event_registry=EventRegistry(),
        message_broker=RabbitMQBroker("amqp://localhost"),
    )

    # Event types
    app.event_registry.register(OrderPlaced)
    app.event_registry.register(OrderCancelled)

    # UoW factory
    def uow_factory() -> OrderUoW:
        return OrderUoW(lambda: create_session(connection_string))

    # Commands
    app.message_bus.register_command(
        PlaceOrder,
        PlaceOrderHandler(PricingService(), InventoryService()),
        uow_factory,
        behaviors=[LoggingBehavior()],
    )
    app.message_bus.register_command(
        CancelOrder,
        CancelOrderHandler(),
        uow_factory,
    )

    # Queries
    app.message_bus.register_query(
        GetOrders,
        GetOrdersHandler(OrderReadStore()),
    )

    # Events
    app.message_bus.register_event(
        OrderPlaced,
        PublishOrderPlacedHandler(broker),
    )

    return app
```

## Expected Outcome

You get a fully wired `Application` instance. Calling `app.dispatch(...)` routes commands through the handler pipeline with transactional UoW lifecycle, and queries through the query pipeline. Domain events are collected after commit and dispatched to registered event handlers.

## See Also

- [Application Bootstrap concept](../../concepts/infrastructure/bootstrap.md)
- [Register Handlers](register-handlers.md) — detailed registration patterns
- [Event Registry how-to](event-registry.md) — registering event types
- [Configure a Message Broker](configure-message-broker.md) — broker setup
