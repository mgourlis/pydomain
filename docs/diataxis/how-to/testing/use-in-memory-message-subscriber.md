# How to Use an In-Memory Message Subscriber

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

### 5. Check lifecycle state

```python
await subscriber.start()
assert subscriber.started is True

await subscriber.stop()
assert subscriber.stopped is True
```

### 6. Wire with InboundEventGateway

```python
from pydomain.infrastructure.message_subscriber import InboundEventGateway


subscriber = InMemoryMessageSubscriber()
gateway = InboundEventGateway(subscriber, app.message_bus)

gateway.register_translation(
    topic="shipping.shipment.failed",
    integration_class=ShipmentFailedIntegrationEvent,
    translator=translate_shipment_failed,
)

# Simulate an incoming message — flows through the full gateway pipeline
await subscriber.simulate_message("shipping.shipment.failed", {
    "order_id": "550e8400-e29b-41d4-a716-446655440000",
    "failure_reason": "Address not found",
})
# → hydration → translation → dispatch → event handlers
```

### 7. Verify the full inbound flow in a test

```python
async def test_inbound_event_flow():
    subscriber = InMemoryMessageSubscriber()
    gateway = InboundEventGateway(subscriber, app.message_bus)

    # Track dispatched events
    dispatched: list[ExternalShipmentFailed] = []

    async def event_handler(event: ExternalShipmentFailed) -> None:
        dispatched.append(event)

    app.message_bus.register_event(ExternalShipmentFailed, event_handler)
    gateway.register_translation(
        "shipping.shipment.failed",
        ShipmentFailedIntegrationEvent,
        translate_shipment_failed,
    )

    # Simulate the external broker
    await subscriber.simulate_message("shipping.shipment.failed", {
        "order_id": "550e8400-e29b-41d4-a716-446655440000",
        "failure_reason": "Address not found",
    })

    assert len(dispatched) == 1
    assert dispatched[0].order_id == UUID("550e8400-e29b-41d4-a716-446655440000")
    assert dispatched[0].reason == "Address not found"
```

## Complete Example

```python
import pytest
from uuid import UUID

from pydomain.testing import InMemoryMessageSubscriber
from pydomain.infrastructure.message_subscriber import InboundEventGateway
from pydomain.infrastructure.bootstrap import bootstrap


class TestInboundMessaging:
    @pytest.fixture
    def subscriber(self) -> InMemoryMessageSubscriber:
        return InMemoryMessageSubscriber()

    @pytest.fixture
    async def app_and_gateway(self, subscriber):
        app = await bootstrap()
        gateway = InboundEventGateway(subscriber, app.message_bus)
        return app, gateway

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
        gateway = InboundEventGateway(subscriber, app.message_bus)

        await gateway.start()
        assert subscriber.started

        await gateway.stop()
        assert subscriber.stopped
```

## Key Methods

| Method | Purpose |
|--------|---------|
| `subscribe(topic, handler)` | Register an async handler for a topic |
| `simulate_message(topic, payload)` | Manually inject a message (not part of the protocol) |
| `start()` | Toggle `started` flag |
| `stop()` | Toggle `stopped` flag |

## Assertion Properties

| Property | Type | Purpose |
|----------|------|---------|
| `subscriptions` | `dict[str, Callable]` | Verify which topics have registered handlers |
| `started` | `bool` | Verify `start()` was called |
| `stopped` | `bool` | Verify `stop()` was called |

## Expected Outcome

Your tests use `InMemoryMessageSubscriber` with `InboundEventGateway` to verify the full inbound messaging flow — from raw JSON payload to dispatched domain event. No external broker needed. Tests are fast, deterministic, and exercise the same code paths as production.

## See Also

- [MessageSubscriber concept](../../concepts/infrastructure/message-subscriber.md)
- [InboundEventGateway concept](../../concepts/infrastructure/inbound-event-gateway.md)
- [Configure a MessageSubscriber](../infrastructure/configure-message-subscriber.md)
- [Configure an InboundEventGateway](../infrastructure/configure-inbound-event-gateway.md)
- [Testing philosophy](../../concepts/testing/testing-philosophy.md)
