# How to Configure a MessageSubscriber

> **Prerequisites:** [MessageSubscriber concept](../../concepts/infrastructure/message-subscriber.md), [InboundEventGateway concept](../../concepts/infrastructure/inbound-event-gateway.md)

## Problem

You need to receive integration events from an external message broker (Kafka, RabbitMQ) so your application can react to events published by other services.

## Solution

Implement the `MessageSubscriber` protocol for your transport, then pass it to an `InboundEventGateway` that hydrates, translates, and dispatches the events into your internal `MessageBus`.

## Steps

### 1. Implement the MessageSubscriber protocol

The protocol requires three methods: `subscribe()`, `start()`, and `stop()`.

**Kafka example:**

```python
from collections.abc import Awaitable, Callable
from typing import Any

from aiokafka import AIOKafkaConsumer

from pydomain.infrastructure.message_subscriber import MessageSubscriber


class KafkaMessageSubscriber:
    def __init__(self, bootstrap_servers: str, group_id: str) -> None:
        self._servers = bootstrap_servers
        self._group_id = group_id
        self._consumer: AIOKafkaConsumer | None = None
        self._handlers: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {}

    def subscribe(
        self, topic: str, handler: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None:
        self._handlers[topic] = handler

    async def start(self) -> None:
        self._consumer = AIOKafkaConsumer(
            *self._handlers.keys(),
            bootstrap_servers=self._servers,
            group_id=self._group_id,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        )
        await self._consumer.start()
        # Begin polling loop (runs as a background task)
        asyncio.create_task(self._poll())

    async def stop(self) -> None:
        if self._consumer:
            await self._consumer.stop()

    async def _poll(self) -> None:
        async for msg in self._consumer:
            handler = self._handlers.get(msg.topic)
            if handler:
                try:
                    await handler(msg.value)
                except Exception:
                    # Dispatch failure — NACK, don't commit offset
                    logger.exception("Handler failed for %s", msg.topic)
                else:
                    # Success — ACK (commit offset handled by consumer)
                    pass
```

**RabbitMQ example:**

```python
import aio_pika


class RabbitMQSubscriber:
    def __init__(self, connection_url: str) -> None:
        self._url = connection_url
        self._connection: aio_pika.Connection | None = None
        self._channel: aio_pika.Channel | None = None
        self._handlers: dict[str, Callable] = {}

    def subscribe(self, topic: str, handler: Callable) -> None:
        self._handlers[topic] = handler

    async def start(self) -> None:
        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel()
        for topic, handler in self._handlers.items():
            queue = await self._channel.declare_queue(topic)
            await queue.consume(
                lambda msg, t=topic, h=handler: self._on_message(t, h, msg)
            )

    async def stop(self) -> None:
        if self._channel:
            await self._channel.close()
        if self._connection:
            await self._connection.close()

    async def _on_message(self, topic: str, handler: Callable, message) -> None:
        try:
            payload = json.loads(message.body)
            await handler(payload)
        except Exception:
            await message.nack(requeue=True)  # retryable
        else:
            await message.ack()
```

The ACK/NACK logic aligns with the [`MessageSubscriber` failure mode contract](../../concepts/infrastructure/message-subscriber.md#failure-mode-contract): if the handler raises, the dispatch failed and the message should be NACKed for retry. If the handler returns normally, the message should be ACKed (validation/translation failures are swallowed by the gateway, so the handler doesn't see them).

### 2. Pass the subscriber to an InboundEventGateway

The subscriber is rarely used directly — it's wrapped by the `InboundEventGateway` which handles hydration, translation, and dispatch:

```python
from pydomain.infrastructure.message_subscriber import InboundEventGateway


subscriber = KafkaMessageSubscriber(
    bootstrap_servers="localhost:9092",
    group_id="order-service",
)

gateway = InboundEventGateway(subscriber, app.message_bus)
```

See [Configure an InboundEventGateway](configure-inbound-event-gateway.md) for the full gateway setup.

### 3. Pass the gateway to bootstrap

```python
from pydomain.infrastructure.bootstrap import bootstrap


gateway = InboundEventGateway(subscriber, app.message_bus)
# ... register translations on gateway ...

app = await bootstrap(inbound_gateways=[gateway])
# → gateway.start() called inside bootstrap()
```

`bootstrap()` calls `start()` on each gateway. `Application.shutdown()` calls `stop()` during graceful shutdown.

## Using the Fake for Tests

```python
import pytest
from tests.infrastructure.test_inbound_event_gateway import FakeMessageSubscriber


@pytest.fixture
def subscriber() -> FakeMessageSubscriber:
    return FakeMessageSubscriber()


@pytest.fixture
def gateway(subscriber, message_bus) -> InboundEventGateway:
    gw = InboundEventGateway(subscriber, message_bus)
    gw.register_translation(
        "shipping.shipment.failed",
        ShipmentFailedIntegrationEvent,
        translate_shipment_failed,
    )
    return gw


async def test_inbound_event_flow(subscriber, gateway):
    await gateway.start()

    # Simulate an incoming message from the broker
    await subscriber.simulate_message("shipping.shipment.failed", {
        "order_id": "550e8400-e29b-41d4-a716-446655440000",
        "failure_reason": "Address not found",
    })

    # Assert the domain event was dispatched
    # ... verify internal state changed ...
```

`FakeMessageSubscriber` implements the `MessageSubscriber` protocol in-memory. Its `simulate_message(topic, payload)` method lets you inject messages as if they arrived from a real broker.

## Complete Example (Kafka inbound)

```python
import json
import asyncio
from aiokafka import AIOKafkaConsumer

from pydomain.infrastructure.bootstrap import bootstrap
from pydomain.infrastructure.message_subscriber import (
    InboundEventGateway,
    MessageSubscriber,
)


class KafkaMessageSubscriber:
    def __init__(self, bootstrap_servers: str, group_id: str) -> None:
        self._servers = bootstrap_servers
        self._group_id = group_id
        self._consumer: AIOKafkaConsumer | None = None
        self._handlers: dict[str, Callable] = {}

    def subscribe(self, topic, handler):
        self._handlers[topic] = handler

    async def start(self) -> None:
        self._consumer = AIOKafkaConsumer(
            *self._handlers.keys(),
            bootstrap_servers=self._servers,
            group_id=self._group_id,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        )
        await self._consumer.start()
        asyncio.create_task(self._poll())

    async def stop(self) -> None:
        if self._consumer:
            await self._consumer.stop()

    async def _poll(self) -> None:
        async for msg in self._consumer:
            handler = self._handlers.get(msg.topic)
            if handler:
                try:
                    await handler(msg.value)
                except Exception:
                    logger.exception("Dispatch failed — will NOT commit offset")


async def main() -> None:
    subscriber = KafkaMessageSubscriber(
        bootstrap_servers="localhost:9092",
        group_id="order-service",
    )

    app = await bootstrap()

    gateway = InboundEventGateway(subscriber, app.message_bus)
    gateway.register_translation(
        "shipping.shipment.failed",
        ShipmentFailedIntegrationEvent,
        translate_shipment_failed,
    )

    # Pass gateways to bootstrap (calls start())
    app = await bootstrap(inbound_gateways=[gateway])

    # ... register handlers, event types ...

    try:
        # Run until shutdown
        await asyncio.Event().wait()
    finally:
        await app.shutdown()
```

## Expected Outcome

Your application receives integration events from the external broker. Each message is hydrated into a typed `IntegrationEvent`, translated to a `DomainEvent`, and dispatched into the `MessageBus` where internal event handlers process it. Validation and translation failures are logged and discarded (ACKed). Dispatch failures propagate (NACKed) for retry.

## See Also

- [MessageSubscriber concept](../../concepts/infrastructure/message-subscriber.md)
- [InboundEventGateway concept](../../concepts/infrastructure/inbound-event-gateway.md)
- [Configure an InboundEventGateway](configure-inbound-event-gateway.md)
- [Bootstrap the Application](bootstrap-application.md)
- [Handle Domain Events](../cqrs/handle-domain-events.md)
