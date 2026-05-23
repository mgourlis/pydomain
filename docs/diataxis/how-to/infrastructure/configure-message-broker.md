# How to Configure a Message Broker

> **Prerequisites:** [Message Broker concept](../../concepts/infrastructure/message-broker.md), [Integration Events concept](../../concepts/cqrs/integration-events.md)

## Problem

You need to publish integration events to an external message broker (RabbitMQ, Kafka, Redis) so other services can react to events from this application.

## Solution

Implement the `MessageBroker` protocol for your transport, pass it to `bootstrap()`, and inject it into event handlers that publish integration events.

## Steps

### 1. Implement the MessageBroker protocol

```python
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

    async def publish(self, topic: str, event: IntegrationEvent) -> None:
        exchange = await self._channel.declare_exchange(
            topic, aio_pika.ExchangeType.TOPIC,
        )
        body = event.model_dump_json().encode()
        await exchange.publish(
            aio_pika.Message(body=body, content_type="application/json"),
            routing_key=topic,
        )
```

The protocol requires three methods: `start()`, `stop()`, and `publish(topic, event)`.

### 2. Pass the broker to bootstrap

```python
from pydomain.infrastructure.bootstrap import bootstrap


broker = RabbitMQBroker(connection_url="amqp://localhost:5672")

app = await bootstrap(message_broker=broker)
# → broker.start() is called inside bootstrap()
```

If you pass a broker, `bootstrap()` calls `start()` — the connection is established before any handler runs.

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

```python
broker = RabbitMQBroker("amqp://localhost")
app = await bootstrap(message_broker=broker)

app.message_bus.register_event(
    OrderPlaced,
    PublishOrderPlacedHandler(broker),
)
```

### 5. Shut down gracefully

```python
try:
    result, events = await app.dispatch(PlaceOrder(...))
finally:
    await broker.stop()
```

Call `stop()` during application shutdown to close connections and flush buffers.

## Using the In-Memory Fake for Tests

```python
from pydomain.testing.in_memory_message_broker import InMemoryMessageBroker


async def test_publishes_integration_event():
    broker = InMemoryMessageBroker()
    await broker.start()

    app = await bootstrap(message_broker=broker)
    app.message_bus.register_event(
        OrderPlaced,
        PublishOrderPlacedHandler(broker),
    )
    app.message_bus.register_command(
        PlaceOrder,
        PlaceOrderHandler(repo),
        lambda: FakeUoW(repo),
    )

    await app.dispatch(PlaceOrder(customer_id="c1", items=[]))

    # Assert: integration event was published
    assert len(broker.published_messages) == 1
    assert broker.published_messages[0].topic == "orders"
    assert broker.published_messages[0].event.order_id is not None
```

## Complete Example (RabbitMQ)

```python
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

    async def publish(self, topic: str, event: IntegrationEvent) -> None:
        exchange = await self._channel.declare_exchange(
            topic, aio_pika.ExchangeType.TOPIC,
        )
        body = event.model_dump_json().encode()
        await exchange.publish(
            aio_pika.Message(body=body, content_type="application/json"),
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
        await broker.stop()
```

## Expected Outcome

When a command produces a domain event, the registered event handler publishes the corresponding integration event to the broker. The broker serializes it as JSON and routes it to the named topic. Downstream services can consume it.

## See Also

- [Message Broker concept](../../concepts/infrastructure/message-broker.md)
- [Implement an Integration Event](../cqrs/implement-integration-event.md)
- [Handle Domain Events](../cqrs/handle-domain-events.md)
- [Bootstrap the Application](bootstrap-application.md)
