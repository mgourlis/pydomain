# How to Bootstrap the Application

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
from pydomain.testing import (
    FakeEventStore,
    FakeSnapshotStore,
    FakeUnitOfWork,
    InMemoryMessageBroker,
    InMemoryMessageSubscriber,
)


class OrderPlaced(DomainEvent):
    order_id: UUID


class OrderShipped(DomainEvent):
    order_id: UUID


class OrderCancelled(DomainEvent):
    order_id: UUID


class ShipmentDeliveredIntegration(IntegrationEvent):
    order_id: str


class PlaceOrder(Command[EmptyCommandResult]):
    customer_id: str


class CancelOrder(Command[EmptyCommandResult]):
    order_id: str


class OrderSummary(QueryResult):
    customer_id: str
    order_count: int


class GetCustomerOrders(Query[OrderSummary]):
    customer_id: str


class PlaceOrderHandler:
    async def __call__(self, command: PlaceOrder, uow: Any) -> EmptyCommandResult:
        return EmptyCommandResult()


class CancelOrderHandler:
    async def __call__(self, command: CancelOrder, uow: Any) -> EmptyCommandResult:
        return EmptyCommandResult()


class GetCustomerOrdersHandler:
    async def __call__(self, query: GetCustomerOrders) -> OrderSummary:
        return OrderSummary(customer_id=query.customer_id, order_count=0)


sent: list[UUID] = []


async def send_order_confirmation(event: OrderPlaced) -> None:
    sent.append(event.order_id)


async def mark_delivered(event: OrderShipped) -> None:
    pass


def create_event_store() -> FakeEventStore:
    return FakeEventStore()


def create_snapshot_store() -> FakeSnapshotStore:
    return FakeSnapshotStore()


def create_broker() -> InMemoryMessageBroker:
    return InMemoryMessageBroker()


def create_subscriber() -> InMemoryMessageSubscriber:
    return InMemoryMessageSubscriber()


OrderUoW = FakeUnitOfWork
-->

> **Prerequisites:** [Application Bootstrap concept](../../concepts/infrastructure/bootstrap.md), [Message Bus concept](../../concepts/infrastructure/message-bus.md)

## Problem

You need to wire all infrastructure components — message bus, event registry, event store, message broker, inbound gateways — into a configured `Application` ready to dispatch commands and queries, and to shut it down cleanly.

## Solution

Create the message bus yourself, build any inbound gateways on it, and call `bootstrap()` with the bus and your infrastructure. Register your handlers and event types on `app.message_bus` and `app.event_registry` after bootstrap returns, and call `app.shutdown()` when the application stops.

## Steps

### 1. Create your infrastructure components

```python
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.infrastructure.event_registry import EventRegistry

# Create the components you need
message_bus = MessageBus()                  # Optional — required with inbound gateways
event_store = create_event_store()          # Optional — for event sourcing
snapshot_store = create_snapshot_store()    # Optional — for snapshots
message_broker = create_broker()            # Optional — for integration events
event_registry = EventRegistry()            # Optional — defaults created if omitted
```

All parameters to `bootstrap()` are optional. Omit what you don't need.

### 2. Build inbound gateways on the same bus

An `InboundEventGateway` dispatches what it receives into the bus you build it with. Use the bus you will pass to `bootstrap()`:

```python
from pydomain.infrastructure.message_subscriber import InboundEventGateway

gateway = InboundEventGateway(create_subscriber(), message_bus)
gateway.register_translation(
    topic="shipping.shipment.delivered",
    integration_class=ShipmentDeliveredIntegration,
    translator=lambda e: OrderShipped(order_id=UUID(e.order_id)),
)
```

Skip this step if the application receives no integration events.

### 3. Call bootstrap()

```python
from pydomain.infrastructure.bootstrap import bootstrap


app = await bootstrap(
    message_bus=message_bus,
    event_store=event_store,
    snapshot_store=snapshot_store,
    message_broker=message_broker,
    event_registry=event_registry,
    inbound_gateways=[gateway],
)
```

`bootstrap()` first checks that every inbound gateway dispatches into `message_bus`. If one does not, or if you pass `inbound_gateways` without `message_bus`, it raises `ValueError` naming the gateway, before anything has started. It then starts the inbound gateways, the broker and the outbound gateway, in that order. If one of them fails to start, or `bootstrap()` is cancelled while starting, the parts already started are stopped and the start error or the cancellation is raised ([ADR-073](../../../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md)).

If you omit `message_bus` (and pass no gateways), a default `MessageBus()` is created. If you omit `event_registry`, a default `EventRegistry()` is created. `app.message_bus` and `app.event_registry` return them.

### 4. Register event types for serialization

```python
app.event_registry.register(OrderPlaced)
app.event_registry.register(OrderShipped)
app.event_registry.register(OrderCancelled)
```

All domain event types that will be persisted or published must be registered.

### 5. Register command handlers

```python
from pydomain.cqrs import LoggingBehavior, ValidationBehavior

# UoW factory — called once per command dispatch
uow_factory = lambda: OrderUoW()

app.message_bus.register_command(
    command_type=PlaceOrder,
    handler=PlaceOrderHandler(),
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

### 6. Register query handlers

```python
app.message_bus.register_query(
    query_type=GetCustomerOrders,
    handler=GetCustomerOrdersHandler(),
)
```

### 7. Register event handlers

```python
app.message_bus.register_event(OrderPlaced, send_order_confirmation)
app.message_bus.register_event(OrderShipped, mark_delivered)
```

Multiple handlers per event type are allowed. Events that arrive through the inbound gateway reach these handlers, because the gateway dispatches into `app.message_bus`.

### 8. Dispatch

```python
# Commands — returns the handler's CommandResult
result = await app.dispatch(PlaceOrder(customer_id="customer-1"))

# Queries — returns the QueryResult
orders = await app.dispatch(GetCustomerOrders(customer_id="customer-1"))
assert orders.order_count == 0
```

### 9. Shut down

```python
await app.shutdown()
```

`shutdown()` stops the inbound gateways, then the outbound gateway, then the broker. Every stop runs even if an earlier one raises; the first error is then re-raised, with a note such as `clean-up also failed: OSError` for each later failure, each failure noted once. A cancellation propagates at once.

## Complete Example

```python
from pydomain.cqrs.behaviors import LoggingBehavior
from pydomain.infrastructure.bootstrap import Application, bootstrap
from pydomain.infrastructure.event_registry import EventRegistry
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.infrastructure.message_subscriber import InboundEventGateway


async def build_app() -> Application:
    bus = MessageBus()

    gateway = InboundEventGateway(create_subscriber(), bus)
    gateway.register_translation(
        topic="shipping.shipment.delivered",
        integration_class=ShipmentDeliveredIntegration,
        translator=lambda e: OrderShipped(order_id=UUID(e.order_id)),
    )

    app = await bootstrap(
        message_bus=bus,
        event_registry=EventRegistry(),
        message_broker=create_broker(),
        inbound_gateways=[gateway],
    )

    # Event types
    app.event_registry.register(OrderPlaced)
    app.event_registry.register(OrderCancelled)

    # UoW factory
    def uow_factory() -> OrderUoW:
        return OrderUoW()

    # Commands
    app.message_bus.register_command(
        PlaceOrder,
        PlaceOrderHandler(),
        uow_factory,
        behaviors=[LoggingBehavior()],
    )
    app.message_bus.register_command(CancelOrder, CancelOrderHandler(), uow_factory)

    # Queries
    app.message_bus.register_query(GetCustomerOrders, GetCustomerOrdersHandler())

    # Events
    app.message_bus.register_event(OrderPlaced, send_order_confirmation)

    return app


app = await build_app()
try:
    await app.dispatch(CancelOrder(order_id=str(uuid4())))
finally:
    await app.shutdown()
```

## Expected Outcome

You get a fully wired `Application` instance. Calling `app.dispatch(...)` routes commands through the handler pipeline with transactional UoW lifecycle, and queries through the query pipeline. Domain events are collected after commit and dispatched to registered event handlers, and so are the events the inbound gateways translate. `app.shutdown()` stops everything `bootstrap()` started.

## See Also

- [Application Bootstrap concept](../../concepts/infrastructure/bootstrap.md)
- [Register Handlers](register-handlers.md) — detailed registration patterns
- [Event Registry how-to](event-registry.md) — registering event types
- [Configure a Message Broker](configure-message-broker.md) — broker setup
- [Configure an InboundEventGateway](configure-inbound-event-gateway.md) — inbound wiring
- [ADR-073](../../../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md) — the wiring check and clean-up rules
