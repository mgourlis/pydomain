# How to Configure an InboundEventGateway

> **Prerequisites:** [InboundEventGateway concept](../../concepts/infrastructure/inbound-event-gateway.md), [MessageSubscriber concept](../../concepts/infrastructure/message-subscriber.md), [Integration Events concept](../../concepts/cqrs/integration-events.md)

## Problem

You need to bridge external integration events into your domain's `MessageBus`. Raw JSON payloads arrive from a message broker and need to be hydrated, translated into domain events, and dispatched to internal handlers — without coupling your domain code to the broker or the external message schema.

## Solution

Create an `InboundEventGateway`, pass it a `MessageSubscriber` and your application's `MessageBus`, then register translations for each topic your application should react to.

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

### 4. Create the gateway and register translations

```python
from pydomain.infrastructure.message_subscriber import InboundEventGateway


subscriber = KafkaMessageSubscriber(
    bootstrap_servers="localhost:9092",
    group_id="order-service",
)

gateway = InboundEventGateway(subscriber, app.message_bus)

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


app = await bootstrap(inbound_gateways=[gateway])
# → gateway.start() called inside bootstrap()
```

`bootstrap()` calls `start()` on each gateway, which delegates to the underlying subscriber. When the application shuts down, `app.shutdown()` calls `stop()` on each gateway.

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

When the gateway dispatches a domain event, the `MessageBus` routes it to all registered handlers — exactly like internally-generated domain events.

## Complete Example

```python
from uuid import UUID

from pydomain.infrastructure.bootstrap import bootstrap
from pydomain.infrastructure.message_subscriber import (
    InboundEventGateway,
    MessageSubscriber,
)
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

    # Bootstrap the app first to get the message bus
    app = await bootstrap()

    # Wire the inbound gateway
    gateway = InboundEventGateway(subscriber, app.message_bus)
    gateway.register_translation(
        topic="shipping.shipment.failed",
        integration_class=ShipmentFailedIntegrationEvent,
        translator=translate_shipment_failed,
    )

    # Re-bootstrap with gateways (starts them)
    app = await bootstrap(inbound_gateways=[gateway])

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
5. Calls `message_bus.dispatch(domain_event)` — the bus routes it to `UpdateOrderStatusOnShipmentFailedHandler`
6. If the handler succeeds, the subscriber ACKs the message
7. If the handler fails, the exception propagates and the subscriber NACKs for retry

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
shipping_gateway = InboundEventGateway(kafka_subscriber, app.message_bus)
shipping_gateway.register_translation(...)

payment_gateway = InboundEventGateway(rabbitmq_subscriber, app.message_bus)
payment_gateway.register_translation(...)

app = await bootstrap(inbound_gateways=[shipping_gateway, payment_gateway])
```

All gateways are started during bootstrap and stopped during shutdown.

## Expected Outcome

Your application receives integration events from external brokers. Each message is automatically hydrated, translated, and dispatched to your domain event handlers. The gateway handles the plumbing — you write the translators and handlers.

## See Also

- [InboundEventGateway concept](../../concepts/infrastructure/inbound-event-gateway.md)
- [MessageSubscriber concept](../../concepts/infrastructure/message-subscriber.md)
- [Configure a MessageSubscriber](configure-message-subscriber.md)
- [Bootstrap the Application](bootstrap-application.md)
- [Implement an Integration Event](../cqrs/implement-integration-event.md)
- [Handle Domain Events](../cqrs/handle-domain-events.md)
