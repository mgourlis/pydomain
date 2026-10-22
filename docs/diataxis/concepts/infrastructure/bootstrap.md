# Application Bootstrap

<!-- docs-test: runnable -->

<!-- docs-test: setup
from typing import Any
from uuid import UUID, uuid4

from pydomain.cqrs import (
    Command,
    EmptyCommandResult,
    IntegrationEvent,
    Query,
    QueryResult,
)
from pydomain.ddd import DomainEvent
from pydomain.infrastructure import EventRegistry, MessageBus
from pydomain.testing import (
    FakeEventStore,
    FakeSnapshotStore,
    FakeUnitOfWork,
    InMemoryMessageBroker,
    InMemoryMessageSubscriber,
)


class OrderPlaced(DomainEvent):
    order_id: UUID
    customer_id: UUID


class OrderShipped(DomainEvent):
    order_id: UUID


class OrderCancelled(DomainEvent):
    order_id: UUID


class OrderPlacedIntegration(IntegrationEvent):
    order_id: str
    customer_id: str


class PlaceOrder(Command[EmptyCommandResult]):
    customer_id: str


class OrderView(QueryResult):
    order_id: str


class GetOrder(Query[OrderView]):
    order_id: str


async def place_order(command: PlaceOrder, uow: Any) -> EmptyCommandResult:
    return EmptyCommandResult()


async def get_order(query: GetOrder) -> OrderView:
    return OrderView(order_id=query.order_id)


async def send_confirmation(event: OrderPlaced) -> None:
    pass


def uow_factory() -> FakeUnitOfWork:
    return FakeUnitOfWork()


store = FakeEventStore()
broker = InMemoryMessageBroker()
subscriber = InMemoryMessageSubscriber()
-->

> **Adoption Level:** 3 — CQRS with Events
> **Module:** `pydomain.infrastructure.bootstrap`

## What is Application Bootstrap?

**Application Bootstrap** is the dependency-injection composition root. It assembles the `MessageBus`, `EventRegistry`, `EventStore`, `MessageBroker` and the inbound and outbound gateways into a wired `Application` instance — ready to dispatch commands and queries.

If the `MessageBus` is the car engine, the bootstrap is the factory floor that puts the car together.

## Why It Exists

Without a bootstrap, wiring a CQRS application requires manual assembly:

<!-- docs-test: skip -->
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
from pydomain.infrastructure import bootstrap

# Bootstrap (declarative, one call)
app = await bootstrap(event_store=store, message_broker=broker)
await app.shutdown()
```

Wiring happens in one place, at startup, rather than lazily. Within one call, each inbound gateway may be listed only once: a gateway listed twice raises `ValueError` before anything starts. Nothing stops you from passing the same gateway to two `bootstrap()` calls, but then it is started twice and either application's `shutdown()` stops it.

## The `Application` Class

<!-- docs-test: skip -->
```python
from pydomain.infrastructure.bootstrap import Application


class Application:
    def __init__(
        self,
        message_bus: MessageBus,
        event_registry: EventRegistry | None = None,  # None → a new EventRegistry()
        snapshot_store: SnapshotStore | None = None,
        message_broker: MessageBroker | None = None,
        inbound_gateways: list[InboundEventGateway] | None = None,
        outbound_gateway: OutboundEventGateway | None = None,
    ) -> None: ...

    @property
    def message_bus(self) -> MessageBus: ...
    @property
    def event_registry(self) -> EventRegistry: ...
    @property
    def snapshot_store(self) -> SnapshotStore | None: ...
    @property
    def message_broker(self) -> MessageBroker | None: ...
    @property
    def inbound_gateways(self) -> list[InboundEventGateway]: ...
    @property
    def outbound_gateway(self) -> OutboundEventGateway | None: ...

    async def dispatch(self, message: Command[Any] | Query[Any] | DomainEvent) -> Any: ...
    async def shutdown(self) -> None: ...
```

`Application` is the thin entry point. Its `dispatch()` method delegates to the `MessageBus`; `shutdown()` stops what `bootstrap()` started. Every property is read-only and returns the object the application was built with (`inbound_gateways` returns a new list). `event_registry` is never `None`: an `Application` built without one creates an empty `EventRegistry`, as `bootstrap()` does ([ADR-073](../../../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md)).

## The `bootstrap()` Function

<!-- docs-test: skip -->
```python
from pydomain.infrastructure.bootstrap import bootstrap


async def bootstrap(
    event_store: EventStore | None = None,
    snapshot_store: SnapshotStore | None = None,
    message_bus: MessageBus | None = None,
    message_broker: MessageBroker | None = None,
    outbound_gateway: OutboundEventGateway | None = None,
    event_registry: EventRegistry | None = None,
    inbound_gateways: list[InboundEventGateway] | None = None,
) -> Application: ...
```

Every parameter is optional. If omitted, sensible defaults are created:

| Parameter | Default (when `None`) | Purpose |
|-----------|----------------------|---------|
| `message_bus` | `MessageBus()` | Central dispatch. **Required** when you pass `inbound_gateways`: it must be the bus the gateways were built with |
| `event_registry` | `EventRegistry()` | Event type registration for serialization |
| `event_store` | `None` | Logged at startup; your repositories use it |
| `snapshot_store` | `None` | Passed through to the `Application` for ES |
| `message_broker` | `None` | If provided, `start()` is called during bootstrap |
| `outbound_gateway` | `None` | If provided, `start()` is called after the broker's |
| `inbound_gateways` | `[]` | Each gateway's `start()` is called first |

## Bootstrap Sequence

```
bootstrap()
  ├── 1. Create defaults for omitted arguments
  │      MessageBus(), EventRegistry()
  ├── 2. Check that every inbound gateway dispatches into the application's bus
  │      and none is listed twice
  │      (ValueError otherwise; nothing has started yet)
  ├── 3. Start each inbound gateway, then the broker, then the outbound gateway
  │      (if one start raises, stop the parts already started, then re-raise)
  ├── 4. Return Application(message_bus, event_registry, snapshot_store, ...)
```

### The wiring check

An `InboundEventGateway` dispatches the events it receives into the bus it was built with (`gateway.message_bus`). If that is not the application's bus, the handlers you register on `app.message_bus` never see those events. `bootstrap()` refuses that wiring before it starts anything:

```python
from pydomain.infrastructure import InboundEventGateway, MessageBus
from pydomain.testing import InMemoryMessageSubscriber

bus = MessageBus()
gateway = InboundEventGateway(InMemoryMessageSubscriber(), bus)

try:
    await bootstrap(inbound_gateways=[gateway])  # no message_bus=: a new bus
except ValueError as error:
    assert str(error) == (
        "inbound_gateways[0] (InboundEventGateway) dispatches into a different "
        "MessageBus than the application's; pass the bus the gateway was built "
        "with as bootstrap(message_bus=...)"
    )
else:
    raise AssertionError("bootstrap() accepted a gateway on another bus")
```

Pass the same bus to both, and the check passes:

```python
app = await bootstrap(message_bus=bus, inbound_gateways=[gateway])
assert app.message_bus is gateway.message_bus
await app.shutdown()
```

### Partial start

If a `start()` raises, `bootstrap()` stops the parts that had already started, in shutdown order, and re-raises the start error. The part whose `start()` raised is not stopped. If a clean-up stop raises as well, the start error carries a note naming that error's type, e.g. `clean-up also failed: RuntimeError`. If `bootstrap()` is cancelled while starting, it does the same clean-up, shielded from the cancellation with `asyncio.shield`, and then re-raises the cancellation. A second cancellation stops the wait for that clean-up, which then finishes in the background. A cancellation that arrives while `bootstrap()` cleans up after a failed start does not interrupt the clean-up either: `bootstrap()` finishes it, then raises the cancellation, with the start error as its context. Because a cancelled `bootstrap()` waits for its clean-up, a stop that hangs keeps an `asyncio.timeout` around `bootstrap()` from completing until the stop returns (the deadline fires, but the `TimeoutError` waits for the stop); under anyio cancel scopes, which cancel again at once, `bootstrap()` re-raises at once and the clean-up finishes in the background. A `KeyboardInterrupt` or `SystemExit` during a start propagates at once, without clean-up, so a Ctrl-C or an exit is never delayed.

## Handler Registration Happens After Bootstrap

The `bootstrap()` function creates the *plumbing*, not the *wiring*. You register your application's handlers on `app.message_bus` after bootstrap returns:

```python
app = await bootstrap(event_store=store, message_broker=broker)

# Register command handlers
app.message_bus.register_command(PlaceOrder, place_order, uow_factory)

# Register query handlers
app.message_bus.register_query(GetOrder, get_order)

# Register event handlers
app.message_bus.register_event(OrderPlaced, send_confirmation)

await app.shutdown()
```

This separation means `bootstrap()` doesn't need to know anything about your domain — it handles infrastructure concerns, and your composition root handles domain wiring.

### Inbound Messaging Wiring

An `InboundEventGateway` needs the bus before `bootstrap()` runs, so create the bus first, build the gateway on it, and pass both to `bootstrap()`. `bootstrap()` starts the gateway (and so its subscriber), and `app.shutdown()` stops it:

```python
from pydomain.infrastructure import InboundEventGateway

bus = MessageBus()
gateway = InboundEventGateway(subscriber, bus)
gateway.register_translation(
    topic="orders",
    integration_class=OrderPlacedIntegration,
    translator=lambda e: OrderPlaced(
        order_id=UUID(e.order_id),
        customer_id=UUID(e.customer_id),
    ),
)

app = await bootstrap(message_bus=bus, inbound_gateways=[gateway])
app.message_bus.register_event(OrderPlaced, send_confirmation)
```

Register the handlers right after `bootstrap()` returns: today the gateways start inside `bootstrap()`, so a subscriber that delivers at once could reach the bus before they exist. The application-lifecycle work will change this.

## Shutdown

`app.shutdown()` stops the inbound gateways first, so no new work arrives, then the outbound gateway, so it drains its last batch, then the broker it publishes through. Every stop runs even when an earlier one raises an `Exception`; afterwards the first error is re-raised, with a note `clean-up also failed: <type>` for each later failure. The notes name the exception type only, never its message. Each failure is noted once, also when two stops or a second `shutdown()` raise the same exception, and the raised error is never noted on itself ([ADR-078](../../../adr/ADR-078-cleanup-errors-stored-versions-and-per-class-configure.md)). A cancellation propagates at once and skips the remaining stops.

```python
await app.shutdown()
```

## Typical Main Entry Point

```python
from pydomain.infrastructure.bootstrap import bootstrap
from pydomain.infrastructure.event_registry import EventRegistry


async def main() -> None:
    # 1. Bootstrap infrastructure
    app = await bootstrap(
        event_store=FakeEventStore(),
        snapshot_store=FakeSnapshotStore(),
        message_broker=InMemoryMessageBroker(),
        event_registry=EventRegistry(),
    )

    # 2. Register your event types for serialization
    app.event_registry.register(OrderPlaced)
    app.event_registry.register(OrderShipped)
    app.event_registry.register(OrderCancelled)

    # 3. Wire your handlers
    app.message_bus.register_command(PlaceOrder, place_order, uow_factory)
    app.message_bus.register_query(GetOrder, get_order)

    # 4. Dispatch, then shut down
    try:
        result = await app.dispatch(PlaceOrder(customer_id=str(uuid4())))
        assert isinstance(result, EmptyCommandResult)
    finally:
        await app.shutdown()


await main()
```

## Design Decision

The bootstrap is deliberately minimal. It doesn't do auto-discovery, convention-based scanning, or decorator registration. Every handler must be explicitly registered. This makes the composition root the single source of truth for what the application does — you can read the wiring to understand the application's capabilities. [ADR-047](../../../adr/ADR-047-bootstrap-composition-root.md) records the composition root; [ADR-073](../../../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md) adds the public accessors, the wiring check, and the clean-up on a failed start or shutdown.

## Next Steps

- **[Bootstrap an Application →](../../how-to/infrastructure/bootstrap-application.md)** — step-by-step wiring
- **[Register Handlers →](../../how-to/infrastructure/register-handlers.md)** — handler registration patterns
- **[Event Registry →](event-registry.md)** — event type registration for serialization
- **[Message Broker →](message-broker.md)** — cross-boundary messaging (outbound)
- **[MessageSubscriber →](message-subscriber.md)** — receiving integration events (inbound)
- **[InboundEventGateway →](inbound-event-gateway.md)** — bridging external brokers to the internal bus
- **[Message Bus →](message-bus.md)** — the central dispatch mechanism
