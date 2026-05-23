# Recipe: Publish Integration Events

> **Adoption Level:** 5 · Prerequisites: [Integration Events concept](../../concepts/cqrs/integration-events.md), [Message Broker concept](../../concepts/infrastructure/message-broker.md), [InboundEventGateway concept](../../concepts/infrastructure/inbound-event-gateway.md)

This recipe shows the full outbound and inbound integration event pipeline: translating domain events into integration events, publishing to a broker, and receiving them on the other side.

## Ingredients

- **Domain events** — standard `DomainEvent` subclasses
- **Integration events** — `IntegrationEvent` subclasses with primitive-only fields
- **Message broker** — `InMemoryMessageBroker` for testing
- **Message subscriber** — `InMemoryMessageSubscriber` for simulating inbound messages
- **InboundEventGateway** — bridges subscriber to the internal message bus

## Step 1: Define the integration event

```python
from pydantic import BaseModel, Field
from pydomain.cqrs.integration_events import IntegrationEvent


class OrderShippedIntegrationEvent(IntegrationEvent):
    order_id: str
    customer_id: str
    shipped_at: str
    carrier: str
    tracking_number: str
```

All fields are primitives — guaranteed to serialize to JSON without custom encoders.

## Step 2: Publish from a domain event handler

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
        await self._broker.publish("orders.shipped", integration_event)
```

The handler translates the domain event's rich types (`UUID`, `datetime`) into strings for the broker.

## Step 3: Verify outbound publishing with InMemoryMessageBroker

```python
from pydomain.testing.in_memory_message_broker import InMemoryMessageBroker

broker = InMemoryMessageBroker()
handler = PublishOrderShippedHandler(broker)

await handler(OrderShipped(
    order_id=UUID(int=1),
    customer_id=UUID(int=10),
    carrier="DHL",
    tracking_number="TRK-123",
))

assert len(broker.published) == 1
topic, event = broker.published[0]
assert topic == "orders.shipped"
assert event.carrier == "DHL"
assert isinstance(event.order_id, str)
```

`InMemoryMessageBroker.published` captures every `(topic, event)` tuple — ideal for assertions in tests.

## Step 4: Receive on the inbound side

On the receiving service, translate the integration event back into a domain event:

```python
from collections.abc import Callable
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

`register_translation` auto-subscribes the topic on the underlying subscriber. The gateway handles hydration, validation, translation, and dispatch.

## Step 6: Simulate an inbound message

```python
await subscriber.simulate_message(
    topic="shipping.shipment.failed",
    payload={
        "event_id": "018f4b2e...",
        "occurred_at": "2025-11-19T10:30:00Z",
        "order_id": "018f4b2e...",
        "reason": "address_unreachable",
        "failed_at": "2025-11-19T10:30:00Z",
    },
)

# The translated domain event was dispatched on the message bus
# and routed to any registered event handlers for ShipmentFailed
```

## Step 7: Bootstrap with the full pipeline

```python
from pydomain.infrastructure.bootstrap import bootstrap

app = await bootstrap(
    message_bus=bus,
    message_broker=kafka_broker,      # Production outbound
    inbound_gateways=[gateway],        # Production inbound
)

# Outbound: domain event → integration event → broker
await bus.dispatch(OrderShipped(...))

# Inbound: broker → subscriber → gateway → domain event → handler
# (handled automatically by the gateway's subscriber)

await app.shutdown()
```

## What we built

A bidirectional integration event pipeline. Outbound: domain event handlers translate and publish to the message broker. Inbound: the `InboundEventGateway` receives raw payloads, hydrates `IntegrationEvent` instances, translates to `DomainEvent` instances, and dispatches on the internal bus. Both sides are testable with in-memory fakes.

## Next steps

- [Configure a Message Broker](../infrastructure/configure-message-broker.md) — production broker setup
- [Configure an InboundEventGateway](../infrastructure/configure-inbound-event-gateway.md) — production inbound setup
- [Catch-Up Subscriptions recipe](subscriptions-catchup.md) — internal event processing

## Cross-references

- **ADR-042**: IntegrationEvent primitive-only constraint
- **ADR-044**: Anti-Corruption Layer via translation functions
