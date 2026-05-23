# How to Register Handlers

> **Prerequisites:** [Handlers concept](../../concepts/cqrs/handlers.md), [Message Bus concept](../../concepts/infrastructure/message-bus.md), [Application Bootstrap concept](../../concepts/infrastructure/bootstrap.md)

## Problem

You need to register command, query, and event handlers on the message bus so that dispatched messages reach the right business logic.

## Solution

Use the `MessageBus.register_command()`, `register_query()`, and `register_event()` methods — each with the appropriate handler, UoW factory (for commands), and optional behaviors.

## Command Handler Registration

```python
app.message_bus.register_command(
    command_type=PlaceOrder,
    handler=PlaceOrderHandler(pricing_service),
    uow_factory=lambda: OrderUoW(session_factory),
    behaviors=[LoggingBehavior(), ValidationBehavior()],
)
```

### Parameters

| Parameter | Required | Purpose |
|-----------|----------|---------|
| `command_type` | Yes | The `Command` subclass this handler handles |
| `handler` | Yes | A `CommandHandler` — callable `(command, uow) → result` |
| `uow_factory` | Yes | A callable `() -> UnitOfWork` — new UoW per dispatch |
| `behaviors` | No | Pipeline behaviors applied in onion order |

### One command, one handler

Each command type can have only one handler. Registering a second raises `HandlerAlreadyRegisteredError`:

```python
bus.register_command(PlaceOrder, handler_a, uow_factory)
bus.register_command(PlaceOrder, handler_b, uow_factory)
# → HandlerAlreadyRegisteredError: PlaceOrder
```

### UoW factory pattern

The `uow_factory` is a zero-argument callable that returns a fresh `UnitOfWork`. It's called for every `dispatch()`, not cached:

```python
# Factory: called per-dispatch for fresh UoW
def order_uow_factory() -> OrderUoW:
    session = session_factory()  # New DB session each time
    return OrderUoW(session)

# Lambda form (equivalent)
uow_factory = lambda: OrderUoW(session_factory)
```

If multiple command types share the same UoW structure, reuse the factory:

```python
uow = lambda: OrderUoW(session_factory)

bus.register_command(PlaceOrder, PlaceOrderHandler(...), uow)
bus.register_command(CancelOrder, CancelOrderHandler(...), uow)
bus.register_command(ShipOrder, ShipOrderHandler(...), uow)
```

### With behaviors

```python
bus.register_command(
    command_type=PlaceOrder,
    handler=handler,
    uow_factory=uow_factory,
    behaviors=[
        LoggingBehavior(),
        ValidationBehavior(),
        IdempotencyBehavior(processed_store),
        AggregateLockingBehavior(lock_provider, key_resolver),
    ],
)
```

Behaviors execute in onion order: first in the list is outermost (runs first, returns last). See [Add a Pipeline Behavior →](../cqrs/add-pipeline-behavior.md).

## Query Handler Registration

```python
app.message_bus.register_query(
    query_type=GetOrder,
    handler=GetOrderHandler(order_read_store),
    behaviors=[LoggingBehavior()],
)
```

### Parameters

| Parameter | Required | Purpose |
|-----------|----------|---------|
| `query_type` | Yes | The `Query` subclass this handler handles |
| `handler` | Yes | A `QueryHandler` — callable `(query) → result` |
| `behaviors` | No | Pipeline behaviors applied in onion order |

No `uow_factory` — queries are read-only.

### One query, one handler

Same constraint as commands — one handler per query type:

```python
bus.register_query(GetOrder, handler_a)
bus.register_query(GetOrder, handler_b)
# → HandlerAlreadyRegisteredError: GetOrder
```

## Event Handler Registration

```python
app.message_bus.register_event(
    event_type=OrderPlaced,
    handler=SendConfirmationHandler(email_service),
)
```

### Multiple handlers per event type

Unlike commands and queries, multiple handlers can be registered for the same event type:

```python
bus.register_event(OrderPlaced, SendConfirmationHandler(email))
bus.register_event(OrderPlaced, UpdateInventoryHandler(inventory))
bus.register_event(OrderPlaced, PublishIntegrationHandler(broker))
```

Handlers execute in registration order. Failure is per-handler — one failing doesn't affect others.

## Registration Order

Registration order matters in two cases:

1. **Event handlers** — execute in the order they were registered
2. **Pipeline behaviors** — execute in onion order (first registered = outermost)

For commands and queries, registration order between different types doesn't matter — only which type maps to which handler.

## Typical Registration Block

```python
# ── Commands ──────────────────────────────────────

uow = lambda: OrderUoW(session_factory)

bus.register_command(PlaceOrder, PlaceOrderHandler(pricing, inventory), uow)
bus.register_command(CancelOrder, CancelOrderHandler(), uow)
bus.register_command(ShipOrder, ShipOrderHandler(logistics), uow)

# ── Queries ───────────────────────────────────────

bus.register_query(GetOrder, GetOrderHandler(orders))
bus.register_query(GetOrdersByCustomer, GetOrdersByCustomerHandler(orders))

# ── Events ────────────────────────────────────────

bus.register_event(OrderPlaced, SendConfirmation(email))
bus.register_event(OrderPlaced, UpdateInventory(inventory))
bus.register_event(OrderPlaced, PublishIntegration(broker))
bus.register_event(OrderShipped, NotifyCustomer(sms))
```

## Expected Outcome

After registration, `app.dispatch(PlaceOrder(...))` routes to `PlaceOrderHandler`, creates a UoW from the factory, runs behaviors, commits, collects events, and dispatches them to registered event handlers.

## See Also

- [Bootstrap the Application](bootstrap-application.md) — full wiring example
- [Configure the Command Bus](../cqrs/configure-command-bus.md) — command bus setup
- [Configure the Query Bus](../cqrs/configure-query-bus.md) — query bus setup
- [Handle Domain Events](../cqrs/handle-domain-events.md) — event handler implementation
- [Message Bus concept](../../concepts/infrastructure/message-bus.md)
