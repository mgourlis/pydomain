# Recipe: Publish Integration Events

<!-- docs-test: runnable -->

<!-- docs-test: setup
from datetime import datetime
from uuid import UUID

from pydomain.ddd.domain_event import DomainEvent


class OrderShipped(DomainEvent):
    order_id: UUID
    customer_id: UUID
    carrier: str
    tracking_number: str


class ShipmentFailed(DomainEvent):
    order_id: UUID
    reason: str
    failed_at: datetime
-->

> **Adoption Level:** 5 · Prerequisites: [Integration Events concept](../../concepts/cqrs/integration-events.md), [Message Broker concept](../../concepts/infrastructure/message-broker.md), [InboundEventGateway concept](../../concepts/infrastructure/inbound-event-gateway.md)

This recipe shows the full outbound and inbound integration event pipeline: translating domain events into integration events, publishing to a broker, and receiving them on the other side.

## Ingredients

- **Domain events** — standard `DomainEvent` subclasses (`OrderShipped`, `ShipmentFailed`)
- **Integration events** — `IntegrationEvent` subclasses with primitive-only fields
- **Message broker** — `InMemoryMessageBroker` for testing
- **Message subscriber** — `InMemoryMessageSubscriber` for simulating inbound messages
- **InboundEventGateway** — bridges subscriber to the internal message bus

## Step 1: Define the integration events

```python
from pydomain.cqrs.integration_events import IntegrationEvent


class OrderShippedIntegrationEvent(IntegrationEvent):
    order_id: str
    customer_id: str
    shipped_at: str
    carrier: str
    tracking_number: str


class ShipmentFailedIntegrationEvent(IntegrationEvent):
    order_id: str
    reason: str
    failed_at: str
```

All fields are primitives — guaranteed to serialize to JSON without custom encoders. The first is published by this service; the second arrives from the shipping service.

## Step 2: Publish from a domain event handler (with tracing)

```python
from pydomain.infrastructure.message_broker import MessageBroker


class PublishOrderShippedHandler:
    def __init__(self, broker: MessageBroker) -> None:
        self._broker = broker

    async def __call__(self, event: OrderShipped) -> None:
        integration_event = OrderShippedIntegrationEvent(
            order_id=str(event.order_id),
            customer_id=str(event.customer_id),
            shipped_at=event.occurred_at.isoformat(),
            carrier=event.carrier,
            tracking_number=event.tracking_number,
        )
        # Stamp tracing IDs so downstream services can continue the trace chain
        stamped = integration_event.stamp_from(event)
        await self._broker.publish("orders.shipped", stamped)
```

The handler translates the domain event's rich types (`UUID`, `datetime`) into strings for the broker, and stamps the integration event with tracing IDs so the workflow can be traced end-to-end across services. `stamp_from()` needs a domain event that carries a `correlation_id`; the unit of work stamps every event it collects, so in a running application the handler always receives one.

A handler publishes after the commit, so a broker outage loses the event. For durable delivery, register the translation with an `OutboundEventRegistry` and let the [outbound event gateway](../infrastructure/configure-outbound-event-gateway.md) publish it from the outbox: you then drop the `stamp_from()` call, because the relay stamps each event with its domain event's `correlation_id` and `event_id` and sends both as `correlation_id` and `causation_id` headers ([ADR-074](../../../adr/ADR-074-outbox-relay-and-registry-honour-their-contracts.md)).

## Step 3: Verify outbound publishing with InMemoryMessageBroker

```python
from pydomain.testing.in_memory_message_broker import InMemoryMessageBroker

broker = InMemoryMessageBroker()
handler = PublishOrderShippedHandler(broker)

# Stamped by hand here; the unit of work does this in a running application.
shipped = OrderShipped(
    order_id=UUID(int=1),
    customer_id=UUID(int=10),
    carrier="DHL",
    tracking_number="TRK-123",
).stamp(correlation_id=UUID(int=100), causation_id=UUID(int=101))

await handler(shipped)

assert len(broker.published) == 1
topic, event, headers = broker.published[0]
assert topic == "orders.shipped"
assert event.carrier == "DHL"
assert isinstance(event.order_id, str)
assert event.correlation_id == str(UUID(int=100))
```

`InMemoryMessageBroker.published` captures every `(topic, event, headers)` tuple — ideal for assertions in tests.

## Step 4: Receive on the inbound side

On the receiving service, translate the integration event back into a domain event:

```python
from pydomain.ddd.domain_event import DomainEvent


def translate_shipment_failed(integration_event: ShipmentFailedIntegrationEvent) -> DomainEvent:
    return ShipmentFailed(
        order_id=UUID(integration_event.order_id),
        reason=integration_event.reason,
        failed_at=datetime.fromisoformat(integration_event.failed_at),
    )
```

## Step 5: Wire the inbound gateway

```python
from pydomain.testing.in_memory_message_subscriber import InMemoryMessageSubscriber
from pydomain.infrastructure.message_subscriber import InboundEventGateway
from pydomain.infrastructure.message_bus import MessageBus

message_bus = MessageBus()
subscriber = InMemoryMessageSubscriber()
gateway = InboundEventGateway(subscriber, message_bus)

gateway.register_translation(
    topic="shipping.shipment.failed",
    integration_class=ShipmentFailedIntegrationEvent,
    translator=translate_shipment_failed,
)
```

`register_translation` auto-subscribes the topic on the underlying subscriber. The gateway handles hydration, validation, translation, and dispatch into `message_bus`, the bus it was built with.

## Step 6: Simulate an inbound message

```python
failures: list[ShipmentFailed] = []


async def record_failure(event: ShipmentFailed) -> None:
    failures.append(event)


message_bus.register_event(ShipmentFailed, record_failure)

await subscriber.simulate_message(
    topic="shipping.shipment.failed",
    payload={
        "event_id": "00000000-0000-4000-8000-000000000201",
        "occurred_at": "2025-11-19T10:30:00Z",
        "order_id": "00000000-0000-4000-8000-000000000001",
        "reason": "address_unreachable",
        "failed_at": "2025-11-19T10:30:00+00:00",
    },
)

# The translated domain event was dispatched on the message bus
# and routed to the handler registered for ShipmentFailed
assert failures[0].reason == "address_unreachable"
```

## Step 7: Bootstrap with the full pipeline

```python
from pydomain.infrastructure.bootstrap import bootstrap

app = await bootstrap(
    message_bus=message_bus,          # the bus the gateway was built with
    message_broker=broker,            # a production broker adapter in production
    inbound_gateways=[gateway],       # production inbound
)

# Outbound: domain event → integration event → broker
app.message_bus.register_event(OrderShipped, PublishOrderShippedHandler(broker))
await app.dispatch(shipped)
assert len(broker.published) == 2

# Inbound: broker → subscriber → gateway → domain event → handler
# (handled automatically by the gateway's subscriber)

await app.shutdown()
```

Pass the bus the gateway was built with as `message_bus=`. `bootstrap()` raises `ValueError` before starting anything when an inbound gateway dispatches into any other bus, including the new one it would build when `message_bus=` is omitted, because events the gateway received would never reach the handlers on the application's bus ([ADR-073](../../../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md)). `app.shutdown()` stops the gateways first, then the broker, and runs every stop even when one fails.

## What we built

A bidirectional integration event pipeline. Outbound: domain event handlers translate and publish to the message broker. Inbound: the `InboundEventGateway` receives raw payloads, hydrates `IntegrationEvent` instances, translates to `DomainEvent` instances, and dispatches on the internal bus. Both sides are testable with in-memory fakes.

## Next steps

- [Configure a Message Broker](../infrastructure/configure-message-broker.md) — production broker setup
- [Configure the outbound event gateway](../infrastructure/configure-outbound-event-gateway.md) — durable outbound delivery through the outbox
- [Configure an InboundEventGateway](../infrastructure/configure-inbound-event-gateway.md) — production inbound setup
- [Catch-Up Subscriptions recipe](subscriptions-catchup.md) — internal event processing

## Cross-references

- **ADR-022**: IntegrationEvent primitive-only constraint
- **ADR-061**: Integration Event Distributed Tracing — `correlation_id` / `causation_id`
- **ADR-044**: Anti-Corruption Layer via translation functions
- **ADR-073**: Safe bootstrap wiring — the gateway's bus must be the application's bus
- **ADR-074**: The outbox relay and registry honour their contracts — tracing applied at delivery
