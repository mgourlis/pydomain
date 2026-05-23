# Handlers

> **Adoption Level:** 2 — CQRS
> **Module:** `pydomain.cqrs.handlers`

## What is a Handler?

A **Handler** is a callable that executes the business logic for a specific message type. Handlers implement **Protocols** (structural subtyping) — any class with the right `__call__` signature qualifies. No base class or decorator is required.

pydomain defines three handler protocols:

| Protocol | Receives | Returns | Side Effects |
|----------|----------|---------|-------------|
| `CommandHandler` | `(command, uow)` | `TResult` (CommandResult) | Mutates aggregates |
| `QueryHandler` | `(query)` | `TResult` (QueryResult) | None (read-only) |
| `EventHandler` | `(event)` | `None` | Fire-and-forget |

## CommandHandler

```python
from pydomain.cqrs.handlers import CommandHandler
from pydomain.cqrs.unit_of_work import UnitOfWork


class CommandHandler[TCommand, TResult](Protocol):
    async def __call__(self, command: TCommand, uow: UnitOfWork) -> TResult:
        """Execute the handler logic inside the given UoW scope."""
        ...
```

The handler receives both the command **and** the Unit of Work. It accesses repositories through the UoW's public attributes:

```python
class PlaceOrderHandler:
    async def __call__(
        self, cmd: PlaceOrder, uow: OrderUoW
    ) -> PlaceOrderResult:
        order = Order.create(cmd.customer_id, cmd.items)
        await uow.orders.add(order)
        return PlaceOrderResult(order_id=order.id, status="placed")
```

The handler must **not** call `uow.commit()` or `uow.rollback()` — the [Command Bus](command-bus.md) manages the lifecycle.

## QueryHandler

```python
from pydomain.cqrs.handlers import QueryHandler


class QueryHandler[TQuery, TResult](Protocol):
    async def __call__(self, query: TQuery) -> TResult:
        """Execute the query logic and return a result."""
        ...
```

Query handlers receive only the query — no Unit of Work:

```python
class GetOrderHandler:
    def __init__(self, read_store: OrderReadStore) -> None:
        self._read_store = read_store

    async def __call__(self, query: GetOrder) -> GetOrderResult:
        data = await self._read_store.get(query.order_id)
        return GetOrderResult(**data)
```

## EventHandler

```python
from pydomain.cqrs.handlers import EventHandler


class EventHandler[TEvent: DomainEvent](Protocol):
    async def __call__(self, event: TEvent) -> None:
        """React to the domain event."""
        ...
```

Event handlers are fire-and-forget — they return `None`. Multiple handlers can be registered for the same event type, and handlers fail independently:

```python
class SendWelcomeEmailHandler:
    def __init__(self, email_service: EmailService) -> None:
        self._email = email_service

    async def __call__(self, event: UserRegistered) -> None:
        await self._email.send_welcome(event.email)
```

For orchestrations (dispatching new commands from an event), inject the `MessageBus`:

```python
class StartOnboardingHandler:
    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus

    async def __call__(self, event: UserRegistered) -> None:
        await self._bus.dispatch(CreateWelcomeDiscount(user_id=event.user_id))
```

## Protocol-Based (No Inheritance)

Handlers use `Protocol` (structural subtyping) — any object with a matching `__call__` signature qualifies. You can use:

- **Classes** with `__init__` for dependency injection (most common)
- **Closures** or **partials** if the handler has no dependencies
- **Functions** decorated with `@functools.partial` for simple cases

```python
# Class-based — preferred for DI
class PlaceOrderHandler:
    def __init__(self, pricing_service: PricingService) -> None:
        self._pricing = pricing_service

    async def __call__(self, cmd: PlaceOrder, uow: OrderUoW) -> PlaceOrderResult:
        ...

# Closure-based — for stateless handlers
async def handle_get_order(query: GetOrder) -> GetOrderResult:
    return GetOrderResult(...)
```

## One Handler Per Message Type

Each message type has exactly one handler. The bus enforces this at registration time — registering a second handler for the same type raises `HandlerAlreadyRegisteredError`.

For events, multiple handlers **are** supported — they are registered via the `MessageBus.register_event()` method and dispatched independently.

## Next Steps

- **[Implement a Command Handler →](../../how-to/cqrs/implement-command-handler.md)** — with DI and UoW
- **[Implement a Query Handler →](../../how-to/cqrs/implement-query-handler.md)** — with read stores
- **[Command Bus →](command-bus.md)** — how handlers are dispatched
- **[Handle Domain Events →](../../how-to/cqrs/handle-domain-events.md)** — event handler guide
