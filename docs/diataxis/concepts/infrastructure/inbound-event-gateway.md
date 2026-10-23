# InboundEventGateway

<!-- docs-test: runnable -->

<!-- docs-test: setup
from uuid import UUID

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing import InMemoryMessageSubscriber


class ShipmentFailedIntegrationEvent(IntegrationEvent):
    order_id: str
    failure_reason: str


class ShipmentDeliveredIntegrationEvent(IntegrationEvent):
    order_id: str
    delivered_at: str


class ExternalShipmentFailed(DomainEvent):
    order_id: UUID
    reason: str


class ExternalShipmentDelivered(DomainEvent):
    order_id: UUID
    delivered_at: str


def translate_shipment_failed(
    event: ShipmentFailedIntegrationEvent,
) -> ExternalShipmentFailed:
    return ExternalShipmentFailed(
        order_id=UUID(event.order_id), reason=event.failure_reason
    )


def translate_shipment_delivered(
    event: ShipmentDeliveredIntegrationEvent,
) -> ExternalShipmentDelivered:
    return ExternalShipmentDelivered(
        order_id=UUID(event.order_id), delivered_at=event.delivered_at
    )


kafka_subscriber = InMemoryMessageSubscriber()
subscriber = InMemoryMessageSubscriber()
-->

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
        ├── 4. derive event_id from the integration event_id (unless the translator set one)
        ├── 5. stamp tracing IDs on domain event (when present)
        └── 6. message_bus.dispatch(domain_event)           → internal handlers
```

**Steps 4-6 are automatic.** The gateway gives the domain event an `event_id` derived from the integration event's (see [Event Identity](#event-identity)). When the integration event carries a `correlation_id`, the gateway stamps it, with the integration event's `event_id` as `causation_id`, onto the translated domain event via `DomainEvent.stamp()`. Translators don't need to know about tracing at all.

## Event Identity

A broker delivers a message at least once, so one integration event can arrive several times. The gateway gives every delivery of it the same domain `event_id`, so a consumer can recognise a redelivery by that id ([ADR-080](../../../adr/ADR-080-identify-delivered-events-for-event-idempotency.md) point 1):

```python
from uuid import NAMESPACE_URL, uuid5

from pydomain.infrastructure.message_bus import MessageBus
from pydomain.infrastructure.message_subscriber import InboundEventGateway

seen = []


async def record(event: ExternalShipmentFailed) -> None:
    seen.append(event.event_id)


bus = MessageBus()
bus.register_event(ExternalShipmentFailed, record)
broker = InMemoryMessageSubscriber()
gateway = InboundEventGateway(broker, bus)
gateway.register_translation(
    "shipping.shipment.failed",
    ShipmentFailedIntegrationEvent,
    translate_shipment_failed,
)

payload = {
    "event_id": "shipment-failed-0001",
    "order_id": "00000000-0000-4000-8000-000000000001",
    "failure_reason": "address not found",
}
await broker.simulate_message("shipping.shipment.failed", payload)
await broker.simulate_message("shipping.shipment.failed", payload)  # a redelivery

namespace = uuid5(NAMESPACE_URL, "https://github.com/mgourlis/pydomain/inbound-event-id")
assert seen == [uuid5(namespace, "shipment-failed-0001")] * 2
```

- **The translator leaves `event_id` out**, as `translate_shipment_failed` does: the gateway replaces the generated id with `uuid5(namespace, integration_event.event_id)`. The namespace is a fixed private constant, `uuid5(NAMESPACE_URL, "https://github.com/mgourlis/pydomain/inbound-event-id")`, which is `52a1e11d-5cfe-52a6-971b-a1b2961dd04f`; it never changes, because stored ids outlive releases. `uuid5` takes any string, so an integration id that is not a UUID works too. The gateway derives the id before it stamps the tracing ids, whether or not the payload has a `correlation_id`.
- **The translator sets `event_id`**: the gateway keeps it. An id counts as set when the translator passes it to the constructor (by name or alias), when a `mode="before"` validator of the event class sets it, or when it is set with `model_copy(update={"event_id": ...})`. `DomainEvent` records whether its validation generated the id; `model_copy()` and `stamp()` keep that record.
- **The translator builds the event with `model_construct()`**: no validator runs, so the id reads as set. The gateway keeps the fresh id, and redeliveries are not recognised. Use the constructor or `model_validate()` in translators.
- **The topic is not part of the derivation.** Integration event ids are unique per producer, so the same id on two topics is a producer bug; both deliveries get one domain `event_id`, and a consumer that deduplicates on it treats the second as a redelivery.

### A Payload Without `event_id`

`IntegrationEvent` fills a missing `event_id` with a new one, so a payload that has none has nothing stable to derive from. The gateway dispatches the translated event with its own id, unchanged, and logs a WARNING that names the integration class and the topic only ([ADR-080](../../../adr/ADR-080-identify-delivered-events-for-event-idempotency.md) point 2, [ADR-071](../../../adr/ADR-071-library-failure-paths-carry-no-payload-values.md)):

```text
WARNING No event_id in the payload of ShipmentFailedIntegrationEvent on topic 'shipping.shipment.failed' — dispatching without deduplication
```

Each delivery then has a new `event_id`, so no redelivery is recognised. pydomain's outbox always writes `event_id`; only foreign producers send such payloads.

## The Class

<!-- docs-test: skip -->
```python
from pydomain.infrastructure.message_subscriber import InboundEventGateway
from pydomain.infrastructure.message_subscriber import MessageSubscriber
from pydomain.infrastructure.message_bus import MessageBus


class InboundEventGateway:
    def __init__(self, subscriber: MessageSubscriber, message_bus: MessageBus) -> None: ...

    @property
    def message_bus(self) -> MessageBus: ...  # read-only: the bus it dispatches into

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

Takes a `MessageSubscriber` (the transport) and a `MessageBus` (the internal dispatcher). Build it on the bus you will pass to `bootstrap()`; the gateway exposes it as the read-only `gateway.message_bus`:

```python
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.infrastructure.message_subscriber import InboundEventGateway


bus = MessageBus()
gateway = InboundEventGateway(kafka_subscriber, bus)
assert gateway.message_bus is bus
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

<!-- docs-test: skip -->
```python
try:
    integration_event = integration_class.model_validate(payload)
except ValidationError as exc:
    logger.error("Failed to validate payload ... — discarding: %s", describe(exc))
    return  # swallowed — poison message
```

**The exception is swallowed.** The payload can never succeed, so the subscriber should ACK it (discard).

The log line names the integration class, the topic and the failing fields with Pydantic's error codes, never the payload's values, and has no traceback ([ADR-071](../../../adr/ADR-071-library-failure-paths-carry-no-payload-values.md)):

```text
ERROR Failed to validate payload for ShipmentFailedIntegrationEvent on topic 'shipping.shipment.failed' — discarding: ValidationError: 1 error for ShipmentFailedIntegrationEvent at failure_reason (missing)
```

The translation and dispatch failures below still log the exception with its traceback; what those lines may contain is planned to be decided with the handler-failure semantics in a later release.

### Translation Failure

The translator function raises an exception:

<!-- docs-test: skip -->
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

<!-- docs-test: skip -->
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

<!-- docs-test: skip -->
```python
if entry is None:
    logger.warning("No translation registered for topic '%s'", topic)
    return  # silently discarded
```

Logged at WARNING and discarded.

## Integration with Bootstrap

The gateway is built before `bootstrap()`, on the bus the application will use, and passed to it:

```python
from pydomain.infrastructure.bootstrap import bootstrap
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.infrastructure.message_subscriber import InboundEventGateway


bus = MessageBus()

gateway = InboundEventGateway(subscriber, bus)
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

app = await bootstrap(message_bus=bus, inbound_gateways=[gateway])  # starts the gateway
assert app.message_bus is gateway.message_bus

await app.shutdown()  # stops the gateway
```

`bootstrap()` checks the wiring before it starts anything: if a gateway's `message_bus` is not the application's bus, it raises `ValueError` naming the gateway (`inbound_gateways[0] (InboundEventGateway) ...`). That includes omitting `message_bus=`, because `bootstrap()` would then build a new bus that the gateway never dispatches into, and its events would be lost. `bootstrap()` then calls `start()` on each gateway; if a later start fails, it stops the gateways it had started before re-raising. `Application.shutdown()` calls `stop()` on each, and keeps stopping the rest when one raises ([ADR-073](../../../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md)).

## Design Decision

The gateway uses a **topic-based dispatch** pattern. The topic string implies the event type — the payload doesn't carry a `type` field. Tracing metadata (`correlation_id`, `causation_id`) travels on the integration event itself (see ADR-061), not in a separate envelope. The integration event's `event_id` identifies the domain event too, through the derivation above (ADR-080).

## Next Steps

- **[Configure an InboundEventGateway →](../../how-to/infrastructure/configure-inbound-event-gateway.md)** — step-by-step wiring guide
- **[Configure a MessageSubscriber →](../../how-to/infrastructure/configure-message-subscriber.md)** — implementing a subscriber
- **[MessageSubscriber Protocol →](message-subscriber.md)** — the subscriber protocol
- **[Application Bootstrap →](bootstrap.md)** — where the gateway is wired
- **[Integration Events →](../cqrs/integration-events.md)** — integration event concepts
- **[Handle Domain Events →](../../how-to/cqrs/handle-domain-events.md)** — event handlers that process the dispatched domain events
