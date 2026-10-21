# How to Register Handlers

<!-- docs-test: runnable -->

<!-- docs-test: setup
from typing import Any
from uuid import UUID

from pydomain.cqrs import (
    Command,
    EmptyCommandResult,
    LoggingBehavior,
    Query,
    QueryResult,
    ValidationBehavior,
)
from pydomain.ddd import DomainEvent
from pydomain.testing import FakeUnitOfWork


class PlaceOrder(Command[EmptyCommandResult]):
    customer_id: str


class OrderView(QueryResult):
    order_id: str


class GetOrder(Query[OrderView]):
    order_id: str


class OrderPlaced(DomainEvent):
    order_id: UUID


class _Handler:
    """Stand-in for the page's handler classes; takes any dependencies."""

    def __init__(self, *dependencies: Any) -> None:
        self.dependencies = dependencies

    async def __call__(self, *args: Any) -> Any:
        return None


PlaceOrderHandler = GetOrderHandler = _Handler
SendConfirmationHandler = UpdateInventoryHandler = PublishIntegrationHandler = _Handler
handler_b = _Handler()
pricing_service = order_read_store = email_service = email = inventory = broker = object()


def session_factory() -> object:
    return object()


class OrderUoW(FakeUnitOfWork):
    def __init__(self, session: object) -> None:
        super().__init__()
-->

> **Prerequisites:** [Handlers concept](../../concepts/cqrs/handlers.md), [Message Bus concept](../../concepts/infrastructure/message-bus.md), [Application Bootstrap concept](../../concepts/infrastructure/bootstrap.md)

## Problem

You need to register command, query, and event handlers on the message bus so that dispatched messages reach the right business logic.

## Solution

Use the `MessageBus.register_command()`, `register_query()`, and `register_event()` methods — each with the appropriate handler, UoW factory (for commands), and optional behaviors.

## Where the Bus Comes From

Register handlers on the application's bus, `app.message_bus`, after `bootstrap()` returns. The examples below call it `bus`:

```python
from pydomain.infrastructure import bootstrap

app = await bootstrap()
bus = app.message_bus
```

If you build inbound gateways, create the bus first, build the gateways on it and pass it as `bootstrap(message_bus=bus, inbound_gateways=[...])`; `app.message_bus` is then that same bus. `bootstrap()` raises `ValueError` if a gateway was built on another bus ([ADR-073](../../../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md)).

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
from pydomain.cqrs import HandlerAlreadyRegisteredError

try:
    bus.register_command(PlaceOrder, handler_b, lambda: OrderUoW(session_factory))
except HandlerAlreadyRegisteredError as error:
    # PlaceOrder already has the handler registered above
    assert str(error) == "Handler already registered for PlaceOrder"
else:
    raise AssertionError("a second PlaceOrder handler was accepted")
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

<!-- docs-test: skip -->
```python
uow = lambda: OrderUoW(session_factory)

bus.register_command(PlaceOrder, PlaceOrderHandler(...), uow)
bus.register_command(CancelOrder, CancelOrderHandler(...), uow)
bus.register_command(ShipOrder, ShipOrderHandler(...), uow)
```

### With behaviors

<!-- docs-test: skip -->
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
try:
    bus.register_query(GetOrder, handler_b)
except HandlerAlreadyRegisteredError as error:
    # GetOrder already has the handler registered above
    assert str(error) == "Handler already registered for GetOrder"
else:
    raise AssertionError("a second GetOrder handler was accepted")
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

<!-- docs-test: skip -->
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

When the application stops, shut it down:

```python
await app.shutdown()
```

## Expected Outcome

After registration, `app.dispatch(PlaceOrder(...))` routes to `PlaceOrderHandler`, creates a UoW from the factory, runs behaviors, commits, collects events, and dispatches them to registered event handlers.

## See Also

- [Bootstrap the Application](bootstrap-application.md) — full wiring example
- [Configure the Command Bus](../cqrs/configure-command-bus.md) — command bus setup
- [Configure the Query Bus](../cqrs/configure-query-bus.md) — query bus setup
- [Handle Domain Events](../cqrs/handle-domain-events.md) — event handler implementation
- [Message Bus concept](../../concepts/infrastructure/message-bus.md)
