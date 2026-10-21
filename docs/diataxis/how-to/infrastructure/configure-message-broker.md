# How to Configure a Message Broker

<!-- docs-test: runnable -->

<!-- docs-test: setup
import sys
import types

from pydantic import BaseModel

# The examples import aio_pika; the docs run uses an empty stand-in module.
sys.modules.setdefault("aio_pika", types.ModuleType("aio_pika"))

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.ddd.domain_event import DomainEvent


class OrderLine(BaseModel):
    product_id: str
    quantity: int


class OrderPlaced(DomainEvent):
    order_id: str
    customer_id: str
    total_amount: int
    items: list[OrderLine] = []


class OrderItemData(BaseModel):
    product_id: str
    quantity: int


class OrderPlacedIntegration(IntegrationEvent):
    order_id: str
    customer_id: str
    total_amount: int
    items: list[OrderItemData]
-->

> **Prerequisites:** [Message Broker concept](../../concepts/infrastructure/message-broker.md), [Integration Events concept](../../concepts/cqrs/integration-events.md)

## Problem

You need to publish integration events to an external message broker (RabbitMQ, Kafka, Redis) so other services can react to events from this application.

## Solution

Implement the `MessageBroker` protocol for your transport, pass it to `bootstrap()`, and inject it into event handlers that publish integration events.

## Steps

### 1. Implement the MessageBroker protocol

```python
from typing import Any

import aio_pika

from pydomain.infrastructure.message_broker import MessageBroker
from pydomain.cqrs.integration_events import IntegrationEvent


class RabbitMQBroker:
    def __init__(self, connection_url: str) -> None:
        self._url = connection_url
        self._connection: aio_pika.Connection | None = None
        self._channel: aio_pika.Channel | None = None

    async def start(self) -> None:
        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel()

    async def stop(self) -> None:
        if self._channel:
            await self._channel.close()
        if self._connection:
            await self._connection.close()

    async def publish(
        self, topic: str, event: IntegrationEvent, **kwargs: Any
    ) -> None:
        exchange = await self._channel.declare_exchange(
            topic, aio_pika.ExchangeType.TOPIC,
        )
        body = event.model_dump_json().encode()
        # The outbox relay passes headers={"correlation_id": ..., "causation_id": ...}
        # for a stamped event (ADR-074); accept and forward them.
        await exchange.publish(
            aio_pika.Message(
                body=body,
                content_type="application/json",
                headers=kwargs.get("headers"),
            ),
            routing_key=topic,
        )
```

The protocol requires three methods: `start()`, `stop()`, and `publish(topic, event, **kwargs)`. Keep `**kwargs`: the [outbound event gateway](configure-outbound-event-gateway.md) passes `headers=` with the event's `correlation_id` and `causation_id`, and a `publish()` that does not accept them fails every delivery.

### 2. Pass the broker to bootstrap

<!-- docs-test: skip -->
```python
from pydomain.infrastructure.bootstrap import bootstrap


broker = RabbitMQBroker(connection_url="amqp://localhost:5672")

app = await bootstrap(message_broker=broker)
# → broker.start() is called inside bootstrap()
```

If you pass a broker, `bootstrap()` calls `start()` — the connection is established before any handler runs. If a start after it fails, such as the outbound gateway's, `bootstrap()` stops the broker and anything else it had started before re-raising ([ADR-073](../../../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md)).

### 3. Create an event handler that publishes to the broker

```python
from pydomain.cqrs.handlers import EventHandler


class PublishOrderPlacedHandler:
    def __init__(self, broker: MessageBroker) -> None:
        self._broker = broker

    async def __call__(self, event: OrderPlaced) -> None:
        integration_event = OrderPlacedIntegration(
            order_id=event.order_id,
            customer_id=event.customer_id,
            total_amount=event.total_amount,
            items=[
                OrderItemData(product_id=item.product_id, quantity=item.quantity)
                for item in event.items
            ],
        )
        await self._broker.publish("orders", integration_event)
```

### 4. Register the handler

<!-- docs-test: skip -->
```python
broker = RabbitMQBroker("amqp://localhost")
app = await bootstrap(message_broker=broker)

app.message_bus.register_event(
    OrderPlaced,
    PublishOrderPlacedHandler(broker),
)
```

### 5. Shut down gracefully

<!-- docs-test: skip -->
```python
try:
    result = await app.dispatch(PlaceOrder(...))
finally:
    await app.shutdown()
```

`app.shutdown()` stops the inbound gateways, then the outbound gateway, then the broker, so the broker closes its connections last. Every stop runs even when an earlier one raises; the first error is re-raised afterwards ([ADR-073](../../../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md)).

## Using the In-Memory Fake for Tests

`InMemoryMessageBroker` records each `publish()` as a `(topic, event, headers)` tuple in `published`:

```python
from pydomain.infrastructure.bootstrap import bootstrap
from pydomain.testing.in_memory_message_broker import InMemoryMessageBroker


broker = InMemoryMessageBroker()
app = await bootstrap(message_broker=broker)  # calls broker.start()
app.message_bus.register_event(
    OrderPlaced,
    PublishOrderPlacedHandler(broker),
)

await app.dispatch(OrderPlaced(order_id="ord-1", customer_id="c1", total_amount=100))

# Assert: integration event was published
assert len(broker.published) == 1
topic, event, _headers = broker.published[0]
assert topic == "orders"
assert event.order_id == "ord-1"

await app.shutdown()
```

In a test suite, dispatch the command that raises `OrderPlaced` instead of the event itself, so the test covers the command handler too.

## Complete Example (RabbitMQ)

<!-- docs-test: skip -->
```python
from typing import Any

import aio_pika
from pydomain.infrastructure.bootstrap import bootstrap
from pydomain.infrastructure.message_broker import MessageBroker
from pydomain.cqrs.integration_events import IntegrationEvent


class RabbitMQBroker:
    def __init__(self, url: str) -> None:
        self._url = url
        self._connection: aio_pika.Connection | None = None
        self._channel: aio_pika.Channel | None = None

    async def start(self) -> None:
        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel()

    async def stop(self) -> None:
        if self._channel:
            await self._channel.close()
        if self._connection:
            await self._connection.close()

    async def publish(
        self, topic: str, event: IntegrationEvent, **kwargs: Any
    ) -> None:
        exchange = await self._channel.declare_exchange(
            topic, aio_pika.ExchangeType.TOPIC,
        )
        body = event.model_dump_json().encode()
        # The outbox relay passes headers={"correlation_id": ..., "causation_id": ...}
        # for a stamped event (ADR-074); accept and forward them.
        await exchange.publish(
            aio_pika.Message(
                body=body,
                content_type="application/json",
                headers=kwargs.get("headers"),
            ),
            routing_key=topic,
        )


async def main() -> None:
    broker = RabbitMQBroker("amqp://localhost:5672")
    app = await bootstrap(message_broker=broker)

    app.event_registry.register(OrderPlaced)
    app.message_bus.register_event(OrderPlaced, PublishOrderPlacedHandler(broker))
    # ... register other handlers

    try:
        await app.dispatch(PlaceOrder(customer_id="c1", items=[]))
    finally:
        await app.shutdown()  # stops the broker
```

## Expected Outcome

When a command produces a domain event, the registered event handler publishes the corresponding integration event to the broker. The broker serializes it as JSON and routes it to the named topic. Downstream services can consume it.

## See Also

- [Message Broker concept](../../concepts/infrastructure/message-broker.md)
- [Implement an Integration Event](../cqrs/implement-integration-event.md)
- [Handle Domain Events](../cqrs/handle-domain-events.md)
- [Bootstrap the Application](bootstrap-application.md)
