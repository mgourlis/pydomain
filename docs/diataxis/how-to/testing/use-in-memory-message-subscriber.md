# How to Use an In-Memory Message Subscriber

<!-- docs-test: runnable -->

<!-- docs-test: setup
from typing import Any
from uuid import UUID

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.ddd.domain_event import DomainEvent


class ShipmentFailedIntegrationEvent(IntegrationEvent):
    order_id: str
    failure_reason: str


class ExternalShipmentFailed(DomainEvent):
    order_id: UUID
    reason: str


def translate_shipment_failed(
    event: ShipmentFailedIntegrationEvent,
) -> ExternalShipmentFailed:
    return ExternalShipmentFailed(
        order_id=UUID(event.order_id), reason=event.failure_reason
    )
-->

> **Prerequisites:** [MessageSubscriber concept](../../concepts/infrastructure/message-subscriber.md), [InboundEventGateway concept](../../concepts/infrastructure/inbound-event-gateway.md), [Testing philosophy](../../concepts/testing/testing-philosophy.md)

## Problem

You need to test inbound event processing — the flow where integration events arrive from an external broker, pass through the `InboundEventGateway` for hydration and translation, and are dispatched into the internal `MessageBus`. You need to simulate incoming messages without connecting to Kafka or RabbitMQ.

## Solution

Use `InMemoryMessageSubscriber` from `pydomain.testing` — an in-memory implementation of the `MessageSubscriber` protocol that records subscriptions and provides `simulate_message()` to inject messages as if they arrived from a real broker.

## Steps

### 1. Import InMemoryMessageSubscriber

```python
from pydomain.testing import InMemoryMessageSubscriber
```

### 2. Create a subscriber

```python
subscriber = InMemoryMessageSubscriber()
# subscriber.subscriptions is {} — topic → handler mappings
# subscriber.started is False
```

### 3. Subscribe a handler

```python
async def handle_order(payload: dict[str, Any]) -> None:
    print(f"Received order: {payload['order_id']}")

subscriber.subscribe("orders", handle_order)
assert "orders" in subscriber.subscriptions
```

### 4. Simulate an incoming message

```python
await subscriber.simulate_message("orders", {
    "order_id": "550e8400-e29b-41d4-a716-446655440000",
    "customer_id": "c1",
})

# The handler was called with the payload
```

A topic with no subscribed handler raises `KeyError`, so a misspelt topic fails the test instead of passing without delivering anything:

```python
import pytest

with pytest.raises(KeyError):
    await subscriber.simulate_message("order", {"order_id": "o1"})  # no handler for "order"
```

`simulate_message()` belongs to the fake, not to the `MessageSubscriber` Protocol: a real broker never hands your subscriber a message on a topic it did not subscribe to.

### 5. Check lifecycle state

```python
await subscriber.start()
assert subscriber.started is True

await subscriber.stop()
assert subscriber.stopped is True
```

### 6. Wire with InboundEventGateway

Build the gateway on a bus, then pass the same bus to `bootstrap()`. `bootstrap()` raises `ValueError` before starting anything when a gateway was built on another bus, or when `message_bus=` is omitted ([ADR-073](../../../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md)):

```python
from pydomain.infrastructure import MessageBus, bootstrap
from pydomain.infrastructure.message_subscriber import InboundEventGateway


bus = MessageBus()
subscriber = InMemoryMessageSubscriber()
gateway = InboundEventGateway(subscriber, bus)

gateway.register_translation(
    topic="shipping.shipment.failed",
    integration_class=ShipmentFailedIntegrationEvent,
    translator=translate_shipment_failed,
)

app = await bootstrap(message_bus=bus, inbound_gateways=[gateway])
assert subscriber.started is True  # bootstrap() started the gateway

# Simulate an incoming message — flows through the full gateway pipeline
await subscriber.simulate_message("shipping.shipment.failed", {
    "order_id": "550e8400-e29b-41d4-a716-446655440000",
    "failure_reason": "Address not found",
})
# → hydration → translation → dispatch → event handlers

await app.shutdown()
assert subscriber.stopped is True
```

### 7. Verify the full inbound flow in a test

```python
async def test_inbound_event_flow():
    bus = MessageBus()
    subscriber = InMemoryMessageSubscriber()
    gateway = InboundEventGateway(subscriber, bus)
    gateway.register_translation(
        "shipping.shipment.failed",
        ShipmentFailedIntegrationEvent,
        translate_shipment_failed,
    )
    app = await bootstrap(message_bus=bus, inbound_gateways=[gateway])

    # Track dispatched events
    dispatched: list[ExternalShipmentFailed] = []

    async def event_handler(event: ExternalShipmentFailed) -> None:
        dispatched.append(event)

    app.message_bus.register_event(ExternalShipmentFailed, event_handler)

    # Simulate the external broker
    await subscriber.simulate_message("shipping.shipment.failed", {
        "order_id": "550e8400-e29b-41d4-a716-446655440000",
        "failure_reason": "Address not found",
    })

    assert len(dispatched) == 1
    assert dispatched[0].order_id == UUID("550e8400-e29b-41d4-a716-446655440000")
    assert dispatched[0].reason == "Address not found"
    await app.shutdown()


await test_inbound_event_flow()
```

## Complete Example

```python
import pytest
from uuid import UUID

from pydomain.testing import InMemoryMessageSubscriber
from pydomain.infrastructure import MessageBus
from pydomain.infrastructure.message_subscriber import InboundEventGateway
from pydomain.infrastructure.bootstrap import bootstrap


class TestInboundMessaging:
    @pytest.fixture
    def subscriber(self) -> InMemoryMessageSubscriber:
        return InMemoryMessageSubscriber()

    @pytest.fixture
    async def app_and_gateway(self, subscriber):
        bus = MessageBus()
        gateway = InboundEventGateway(subscriber, bus)
        app = await bootstrap(message_bus=bus, inbound_gateways=[gateway])
        yield app, gateway
        await app.shutdown()

    async def test_subscribe_records_handler(self, subscriber):
        async def my_handler(payload): ...

        subscriber.subscribe("orders", my_handler)
        assert "orders" in subscriber.subscriptions
        assert subscriber.subscriptions["orders"] is my_handler

    async def test_simulate_message_calls_handler(self, subscriber):
        received: list[dict] = []

        async def handler(payload):
            received.append(payload)

        subscriber.subscribe("orders", handler)
        await subscriber.simulate_message("orders", {"order_id": "o1"})

        assert len(received) == 1
        assert received[0]["order_id"] == "o1"

    async def test_start_stop_flags(self, subscriber):
        assert not subscriber.started
        assert not subscriber.stopped

        await subscriber.start()
        assert subscriber.started

        await subscriber.stop()
        assert subscriber.stopped

    async def test_full_gateway_flow(self, subscriber, app_and_gateway):
        app, gateway = app_and_gateway

        dispatched: list[ExternalShipmentFailed] = []

        async def handler(event: ExternalShipmentFailed) -> None:
            dispatched.append(event)

        app.message_bus.register_event(ExternalShipmentFailed, handler)
        gateway.register_translation(
            "shipping.shipment.failed",
            ShipmentFailedIntegrationEvent,
            translate_shipment_failed,
        )

        await subscriber.simulate_message("shipping.shipment.failed", {
            "order_id": "550e8400-e29b-41d4-a716-446655440000",
            "failure_reason": "Address not found",
        })

        assert len(dispatched) == 1
        assert dispatched[0].reason == "Address not found"

    async def test_unregistered_topic_raises(self, subscriber):
        with pytest.raises(KeyError):
            await subscriber.simulate_message("nonexistent", {})

    async def test_multiple_subscriptions(self, subscriber):
        calls: list[str] = []

        async def handler_a(payload):
            calls.append("a")

        async def handler_b(payload):
            calls.append("b")

        subscriber.subscribe("topic-a", handler_a)
        subscriber.subscribe("topic-b", handler_b)

        await subscriber.simulate_message("topic-a", {})
        await subscriber.simulate_message("topic-b", {})

        assert calls == ["a", "b"]

    async def test_gateway_start_stop_delegates(self, subscriber):
        gateway = InboundEventGateway(subscriber, MessageBus())

        await gateway.start()
        assert subscriber.started

        await gateway.stop()
        assert subscriber.stopped
```

<!-- docs-test: check
suite = TestInboundMessaging()
for name in (
    "test_subscribe_records_handler",
    "test_simulate_message_calls_handler",
    "test_start_stop_flags",
    "test_unregistered_topic_raises",
    "test_multiple_subscriptions",
    "test_gateway_start_stop_delegates",
):
    await getattr(suite, name)(InMemoryMessageSubscriber())

check_subscriber = InMemoryMessageSubscriber()
check_bus = MessageBus()
check_gateway = InboundEventGateway(check_subscriber, check_bus)
check_app = await bootstrap(message_bus=check_bus, inbound_gateways=[check_gateway])
try:
    await suite.test_full_gateway_flow(check_subscriber, (check_app, check_gateway))
finally:
    await check_app.shutdown()
-->

## Key Methods

| Method | Purpose |
|--------|---------|
| `subscribe(topic, handler)` | Register an async handler for a topic |
| `simulate_message(topic, payload)` | Manually inject a message (not part of the protocol); raises `KeyError` if no handler is subscribed to `topic` |
| `start()` | Set the `started` flag |
| `stop()` | Set the `stopped` flag |

## Assertion Properties

| Property | Type | Purpose |
|----------|------|---------|
| `subscriptions` | `dict[str, Callable]` | Verify which topics have registered handlers |
| `started` | `bool` | Verify `start()` was called |
| `stopped` | `bool` | Verify `stop()` was called |

## Expected Outcome

Your tests use `InMemoryMessageSubscriber` with `InboundEventGateway` to verify the full inbound messaging flow — from raw JSON payload to dispatched domain event. The fake keeps the promises of its docstring and passes `MessageSubscriberContract` ([ADR-072](../../../adr/ADR-072-shipped-fakes-conform-to-their-protocol-contracts.md)). No external broker needed. Tests are fast, deterministic, and exercise the same code paths as production.

## See Also

- [MessageSubscriber concept](../../concepts/infrastructure/message-subscriber.md)
- [InboundEventGateway concept](../../concepts/infrastructure/inbound-event-gateway.md)
- [Configure a MessageSubscriber](../infrastructure/configure-message-subscriber.md)
- [Configure an InboundEventGateway](../infrastructure/configure-inbound-event-gateway.md)
- [Testing philosophy](../../concepts/testing/testing-philosophy.md)
