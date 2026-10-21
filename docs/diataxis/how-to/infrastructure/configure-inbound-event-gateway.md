# How to Configure an InboundEventGateway

<!-- docs-test: runnable -->

<!-- docs-test: setup
from uuid import UUID

from pydomain.testing import InMemoryMessageSubscriber


class KafkaMessageSubscriber(InMemoryMessageSubscriber):
    """Stand-in for the Kafka adapter of Configure a MessageSubscriber."""

    def __init__(self, bootstrap_servers: str, group_id: str) -> None:
        super().__init__()


class RabbitMQMessageSubscriber(InMemoryMessageSubscriber):
    """Stand-in for the RabbitMQ adapter of Configure a MessageSubscriber."""

    def __init__(self, url: str, queue: str) -> None:
        super().__init__()


class Order:
    def __init__(self, order_id: UUID) -> None:
        self.order_id = order_id
        self.status = "placed"

    def mark_shipment_failed(self, reason: str) -> None:
        self.status = "shipment-failed"

    def mark_delivered(self) -> None:
        self.status = "delivered"


class OrderRepository:
    def __init__(self) -> None:
        self._orders: dict[UUID, Order] = {}

    async def get_by_id(self, order_id: UUID) -> Order:
        return self._orders.setdefault(order_id, Order(order_id))

    async def save(self, order: Order) -> None:
        self._orders[order.order_id] = order


class UpdateOrderStatusOnShipmentFailedHandler:
    def __init__(self, repo: OrderRepository) -> None:
        self._repo = repo

    async def __call__(self, event) -> None:
        order = await self._repo.get_by_id(event.order_id)
        order.mark_shipment_failed(event.reason)
        await self._repo.save(order)


class UpdateOrderStatusOnDeliveredHandler:
    def __init__(self, repo: OrderRepository) -> None:
        self._repo = repo

    async def __call__(self, event) -> None:
        order = await self._repo.get_by_id(event.order_id)
        order.mark_delivered()
        await self._repo.save(order)


repo = OrderRepository()
order_repo = repo
-->

> **Prerequisites:** [InboundEventGateway concept](../../concepts/infrastructure/inbound-event-gateway.md), [MessageSubscriber concept](../../concepts/infrastructure/message-subscriber.md), [Integration Events concept](../../concepts/cqrs/integration-events.md)

## Problem

You need to bridge external integration events into your domain's `MessageBus`. Raw JSON payloads arrive from a message broker and need to be hydrated, translated into domain events, and dispatched to internal handlers — without coupling your domain code to the broker or the external message schema.

## Solution

Create the application's `MessageBus` first. Build an `InboundEventGateway` on a `MessageSubscriber` and that bus, register a translation for each topic your application should react to, and pass the same bus and the gateway to `bootstrap()`.

## Steps

### 1. Define your integration events

Integration events use primitive types only — the external contract:

```python
from pydomain.cqrs.integration_events import IntegrationEvent


class ShipmentFailedIntegrationEvent(IntegrationEvent):
    order_id: str
    failure_reason: str


class ShipmentDeliveredIntegrationEvent(IntegrationEvent):
    order_id: str
    delivered_at: str  # ISO 8601
```

For detailed guidance, see [Implement an Integration Event](../cqrs/implement-integration-event.md).

### 2. Define your domain events

These are the internal events your handlers react to:

```python
from uuid import UUID

from pydomain.ddd.domain_event import DomainEvent


class ExternalShipmentFailed(DomainEvent):
    order_id: UUID
    reason: str


class ExternalShipmentDelivered(DomainEvent):
    order_id: UUID
    delivered_at: str
```

### 3. Write translators (Anti-Corruption Layer)

Each translator converts an integration event to a domain event, translating primitive fields into rich domain types:

```python
def translate_shipment_failed(
    event: ShipmentFailedIntegrationEvent,
) -> ExternalShipmentFailed:
    return ExternalShipmentFailed(
        order_id=UUID(event.order_id),
        reason=event.failure_reason,
    )


def translate_shipment_delivered(
    event: ShipmentDeliveredIntegrationEvent,
) -> ExternalShipmentDelivered:
    return ExternalShipmentDelivered(
        order_id=UUID(event.order_id),
        delivered_at=event.delivered_at,
    )
```

Translators are plain functions — no base class or registration needed. The gateway calls them with a hydrated `IntegrationEvent` and expects a `DomainEvent` back.

### 4. Create the bus, the gateway, and register translations

The gateway dispatches into the bus you build it with, and the application does not exist yet, so create the bus first. You pass the same bus to `bootstrap()` in step 5:

```python
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.infrastructure.message_subscriber import InboundEventGateway


bus = MessageBus()

subscriber = KafkaMessageSubscriber(
    bootstrap_servers="localhost:9092",
    group_id="order-service",
)

gateway = InboundEventGateway(subscriber, bus)

gateway.register_translation(
    topic="shipping.shipment.failed",
    integration_class=ShipmentFailedIntegrationEvent,
    translator=translate_shipment_failed,
)

gateway.register_translation(
    topic="shipping.shipment.delivered",
    integration_class=ShipmentDeliveredIntegrationEvent,
    translator=translate_shipment_delivered,
)
```

Each `register_translation()` call does two things:

1. Stores the `(integration_class, translator)` pair keyed by topic
2. Calls `subscriber.subscribe(topic, handler)` to start receiving messages on that topic

Re-registering the same topic replaces the previous translation.

### 5. Pass gateways to bootstrap

```python
from pydomain.infrastructure.bootstrap import bootstrap


app = await bootstrap(message_bus=bus, inbound_gateways=[gateway])
# → gateway.start() called inside bootstrap()
assert app.message_bus is gateway.message_bus
```

Pass the bus the gateway was built with as `message_bus=`. `bootstrap()` checks every gateway before it starts anything: if a gateway's `message_bus` is not the application's bus, or you omit `message_bus=` (which would create a new bus), it raises `ValueError` naming the gateway, e.g. `inbound_gateways[0] (InboundEventGateway) dispatches into a different MessageBus than the application's`. Without the check, the gateway's events would go to a bus no handler is registered on and be lost ([ADR-073](../../../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md)).

`bootstrap()` then calls `start()` on each gateway, which delegates to the underlying subscriber. If a later start fails, the gateways already started are stopped again before the error is raised. When the application shuts down, `app.shutdown()` calls `stop()` on each gateway, and on everything after it even when one stop raises.

### 6. Register event handlers for the domain events

```python
app.message_bus.register_event(
    ExternalShipmentFailed,
    UpdateOrderStatusOnShipmentFailedHandler(repo),
)

app.message_bus.register_event(
    ExternalShipmentDelivered,
    UpdateOrderStatusOnDeliveredHandler(repo),
)
```

`app.message_bus` is the bus the gateway dispatches into, so when the gateway dispatches a domain event, the `MessageBus` routes it to all registered handlers — exactly like internally-generated domain events. Register the handlers right after `bootstrap()` returns: the gateways have already started, so a subscriber that delivers at once could reach the bus first (INF-05, decided later by the application-lifecycle work).

## Complete Example

```python
import asyncio
from uuid import UUID

from pydomain.infrastructure.bootstrap import bootstrap
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.infrastructure.message_subscriber import InboundEventGateway
from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.ddd.domain_event import DomainEvent


# ── Integration Events (external contract) ──────────────────────

class ShipmentFailedIntegrationEvent(IntegrationEvent):
    order_id: str
    failure_reason: str


# ── Domain Events (internal) ────────────────────────────────────

class ExternalShipmentFailed(DomainEvent):
    order_id: UUID
    reason: str


# ── Anti-Corruption Layer ───────────────────────────────────────

def translate_shipment_failed(
    event: ShipmentFailedIntegrationEvent,
) -> ExternalShipmentFailed:
    return ExternalShipmentFailed(
        order_id=UUID(event.order_id),
        reason=event.failure_reason,
    )


# ── Event Handler ───────────────────────────────────────────────

class UpdateOrderStatusOnShipmentFailedHandler:
    def __init__(self, repo: OrderRepository) -> None:
        self._repo = repo

    async def __call__(self, event: ExternalShipmentFailed) -> None:
        order = await self._repo.get_by_id(event.order_id)
        order.mark_shipment_failed(event.reason)
        await self._repo.save(order)


# ── Composition Root ────────────────────────────────────────────

async def main() -> None:
    subscriber = KafkaMessageSubscriber(
        bootstrap_servers="localhost:9092",
        group_id="order-service",
    )

    # One bus for the gateway and the application
    bus = MessageBus()

    # Wire the inbound gateway on that bus
    gateway = InboundEventGateway(subscriber, bus)
    gateway.register_translation(
        topic="shipping.shipment.failed",
        integration_class=ShipmentFailedIntegrationEvent,
        translator=translate_shipment_failed,
    )

    # Bootstrap with the same bus (starts the gateway)
    app = await bootstrap(message_bus=bus, inbound_gateways=[gateway])

    # Register event types and handlers
    app.event_registry.register(ExternalShipmentFailed)
    app.message_bus.register_event(
        ExternalShipmentFailed,
        UpdateOrderStatusOnShipmentFailedHandler(order_repo),
    )

    try:
        # Application is now receiving inbound events
        await asyncio.Event().wait()
    finally:
        await app.shutdown()
```

## Understanding the Message Flow

When a message arrives on the `"shipping.shipment.failed"` topic:

1. The `KafkaMessageSubscriber` receives the raw JSON bytes
2. It calls the handler registered by the gateway
3. `_process_message()` hydrates `ShipmentFailedIntegrationEvent` via `model_validate`
4. Calls `translate_shipment_failed()` to produce an `ExternalShipmentFailed` domain event
5. **Auto-stamps** the domain event with `correlation_id`/`causation_id` from the integration event (when present)
6. Calls `message_bus.dispatch(domain_event)` — the bus routes it to `UpdateOrderStatusOnShipmentFailedHandler`
7. If the handler succeeds, the subscriber ACKs the message
8. If the handler fails, the exception propagates and the subscriber NACKs for retry

**No changes needed in your translator** — the gateway reads tracing IDs from the incoming integration event automatically. If the publisher stamped the integration event with `correlation_id`/`causation_id` (see [ADR-061](../../../adr/ADR-061-integration-event-tracing.md)), the trace chain continues seamlessly into your domain handlers.

## Failure Handling

The gateway handles three failure modes automatically:

| What fails | Behavior | No code needed |
|------------|----------|----------------|
| Payload doesn't match integration event schema | Logged at ERROR, discarded | Pydantic `ValidationError` caught internally |
| Translator raises an exception | Logged at ERROR, discarded | `Exception` from translator caught internally |
| Event handler raises an exception | Logged at ERROR, **propagated** | Exception re-raised for NACK/retry |

You don't need to add try/except blocks in your translators or handlers for these — the gateway handles them according to the failure mode contract.

## Multiple Gateways

You can have multiple gateways for different event sources:

```python
bus = MessageBus()

kafka_subscriber = KafkaMessageSubscriber(
    bootstrap_servers="localhost:9092", group_id="order-service"
)
rabbitmq_subscriber = RabbitMQMessageSubscriber(
    url="amqp://localhost", queue="order-service"
)

shipping_gateway = InboundEventGateway(kafka_subscriber, bus)
shipping_gateway.register_translation(
    topic="shipping.shipment.failed",
    integration_class=ShipmentFailedIntegrationEvent,
    translator=translate_shipment_failed,
)

payment_gateway = InboundEventGateway(rabbitmq_subscriber, bus)
payment_gateway.register_translation(
    topic="shipping.shipment.delivered",
    integration_class=ShipmentDeliveredIntegrationEvent,
    translator=translate_shipment_delivered,
)

app = await bootstrap(
    message_bus=bus, inbound_gateways=[shipping_gateway, payment_gateway]
)
await app.shutdown()
```

Every gateway must be built on the bus you pass to `bootstrap()`. All gateways are started during bootstrap, in list order, and stopped during shutdown, in the same order.

## Expected Outcome

Your application receives integration events from external brokers. Each message is automatically hydrated, translated, and dispatched to your domain event handlers. The gateway handles the plumbing — you write the translators and handlers.

## See Also

- [InboundEventGateway concept](../../concepts/infrastructure/inbound-event-gateway.md)
- [MessageSubscriber concept](../../concepts/infrastructure/message-subscriber.md)
- [Configure a MessageSubscriber](configure-message-subscriber.md)
- [Bootstrap the Application](bootstrap-application.md)
- [ADR-073](../../../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md) — the bus check and clean-up rules
- [Implement an Integration Event](../cqrs/implement-integration-event.md)
- [Handle Domain Events](../cqrs/handle-domain-events.md)
