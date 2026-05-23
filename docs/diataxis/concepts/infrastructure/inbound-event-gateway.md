# InboundEventGateway

> **Adoption Level:** 3 — CQRS with Events
> **Module:** `pydomain.infrastructure.message_subscriber`

## What is the InboundEventGateway?

The **InboundEventGateway** bridges external message brokers to the internal domain [`MessageBus`](message-bus.md). It receives raw JSON payloads from a [`MessageSubscriber`](message-subscriber.md), hydrates them into typed `IntegrationEvent` instances, translates them to `DomainEvent` instances via an Anti-Corruption Layer translator, and dispatches them into the `MessageBus` for internal routing.

If the `MessageSubscriber` is the network card, the `InboundEventGateway` is the driver that translates raw bytes into application-level events.

## Why It Exists

External messages arrive as raw JSON dicts — no type information, no domain semantics. Three transformations are needed before they can enter the domain:

1. **Hydration** — raw dict → typed `IntegrationEvent` (Pydantic validation)
2. **Translation** — `IntegrationEvent` → `DomainEvent` (Anti-Corruption Layer)
3. **Dispatch** — `DomainEvent` → `MessageBus` (internal routing)

Doing this in a single gateway class rather than in each handler keeps the translation logic centralized and the handlers focused on domain behavior.

## Architecture

```
External Broker (Kafka/RabbitMQ)
        │
        ▼
  MessageSubscriber.subscribe(topic, handler)
        │
        ▼
  InboundEventGateway._process_message(topic, payload)
        │
        ├── 1. Look up (IntegrationEvent class, translator) for topic
        ├── 2. integration_class.model_validate(payload)   → IntegrationEvent
        ├── 3. translator(integration_event)               → DomainEvent
        └── 4. message_bus.dispatch(domain_event)           → internal handlers
```

## The Class

```python
from pydomain.infrastructure.message_subscriber import InboundEventGateway
from pydomain.infrastructure.message_subscriber import MessageSubscriber
from pydomain.infrastructure.message_bus import MessageBus


class InboundEventGateway:
    def __init__(self, subscriber: MessageSubscriber, message_bus: MessageBus) -> None: ...

    def register_translation[T: IntegrationEvent](
        self,
        topic: str,
        integration_class: type[T],
        translator: Callable[[T], DomainEvent],
    ) -> None: ...

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
```

### Constructor

Takes a `MessageSubscriber` (the transport) and a `MessageBus` (the internal dispatcher):

```python
gateway = InboundEventGateway(kafka_subscriber, app.message_bus)
```

### `register_translation(topic, integration_class, translator)`

Register a mapping from topic to domain event. Each call:

1. Stores the `(integration_class, translator)` pair keyed by topic
2. Subscribes to the topic on the underlying `MessageSubscriber`

```python
gateway.register_translation(
    topic="shipping.shipment.failed",
    integration_class=ShipmentFailedIntegrationEvent,
    translator=translate_shipment_failed,
)
```

The **translator** is the Anti-Corruption Layer — it converts the integration event's primitive fields into rich domain types:

```python
class ShipmentFailedIntegrationEvent(IntegrationEvent):
    order_id: str          # primitive in external contract
    failure_reason: str


class ExternalShipmentFailed(DomainEvent):
    order_id: UUID         # rich domain type
    reason: str


def translate_shipment_failed(
    event: ShipmentFailedIntegrationEvent,
) -> ExternalShipmentFailed:
    return ExternalShipmentFailed(
        order_id=UUID(event.order_id),
        reason=event.failure_reason,
    )
```

Re-registering the same topic replaces the previous translation.

## Failure Modes

The gateway distinguishes three failure modes with different semantics:

### Validation Failure

A payload that doesn't match the `IntegrationEvent` schema (missing fields, wrong types):

```python
try:
    integration_event = integration_class.model_validate(payload)
except ValidationError:
    logger.exception("Failed to validate payload ...")
    return  # swallowed — poison message
```

**The exception is swallowed.** The payload can never succeed, so the subscriber should ACK it (discard).

### Translation Failure

The translator function raises an exception:

```python
try:
    domain_event = translator(integration_event)
except Exception:
    logger.exception("Translation failed ...")
    return  # swallowed — translator bug or data issue
```

**The exception is swallowed.** Like validation failures, this indicates a bug or bad data that won't fix itself on retry.

### Dispatch Failure

A domain event handler raises an exception:

```python
try:
    await self._message_bus.dispatch(domain_event)
except Exception:
    logger.exception("Dispatch failed ...")
    raise  # propagated — handler may recover
```

**The exception propagates.** The subscriber should NACK the message so it can be retried — the handler failure may be transient.

### Unknown Topic

A message arrives on a topic with no registered translation:

```python
if entry is None:
    logger.warning("No translation registered for topic '%s'", topic)
    return  # silently discarded
```

Logged at WARNING and discarded.

## Integration with Bootstrap

The gateway is wired after `bootstrap()`, not inside it:

```python
from pydomain.infrastructure.bootstrap import bootstrap
from pydomain.infrastructure.message_subscriber import InboundEventGateway


app = await bootstrap()

gateway = InboundEventGateway(subscriber, app.message_bus)
gateway.register_translation(
    "shipping.shipment.failed",
    ShipmentFailedIntegrationEvent,
    translate_shipment_failed,
)
gateway.register_translation(
    "shipping.shipment.delivered",
    ShipmentDeliveredIntegrationEvent,
    translate_shipment_delivered,
)

await gateway.start()
```

The `Application` class accepts an `inbound_gateways: list[InboundEventGateway]` parameter. When provided, `bootstrap()` calls `start()` on each and `Application.shutdown()` calls `stop()` on each.

## Design Decision

The gateway uses a **topic-based dispatch** pattern rather than an envelope pattern. The topic string implies the event type — the payload doesn't carry a `type` field. This is simpler (no envelope schema to agree on) but requires that each topic carries exactly one integration event type.

## Next Steps

- **[Configure an InboundEventGateway →](../../how-to/infrastructure/configure-inbound-event-gateway.md)** — step-by-step wiring guide
- **[Configure a MessageSubscriber →](../../how-to/infrastructure/configure-message-subscriber.md)** — implementing a subscriber
- **[MessageSubscriber Protocol →](message-subscriber.md)** — the subscriber protocol
- **[Application Bootstrap →](bootstrap.md)** — where the gateway is wired
- **[Integration Events →](../cqrs/integration-events.md)** — integration event concepts
- **[Handle Domain Events →](../../how-to/cqrs/handle-domain-events.md)** — event handlers that process the dispatched domain events
