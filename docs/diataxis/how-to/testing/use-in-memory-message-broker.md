# How to Use an In-Memory Message Broker

> **Prerequisites:** [Message Broker concept](../../concepts/infrastructure/message-broker.md), [Integration Events concept](../../concepts/cqrs/integration-events.md), [Testing philosophy](../../concepts/testing/testing-philosophy.md)

## Problem

You need to verify that your event handlers publish integration events — without connecting to RabbitMQ, Kafka, or any external broker. Tests must capture published events and assert on topic, payload, and ordering.

## Solution

Use `InMemoryMessageBroker` from `pydomain.testing` — an in-memory recorder that captures every `publish()` call in a public `published` list. `start()` and `stop()` are no-ops.

## Steps

### 1. Import InMemoryMessageBroker

```python
from pydomain.testing import InMemoryMessageBroker
```

### 2. Create a broker

```python
broker = InMemoryMessageBroker()
# broker.published is [] — the captured messages list
```

### 3. Publish and assert

```python
await broker.publish("orders", OrderPlacedIntegration(
    order_id="o1",
    customer_id="c1",
    total_amount=Decimal("99.99"),
))

assert len(broker.published) == 1
assert broker.published[0][0] == "orders"          # topic
assert broker.published[0][1].order_id == "o1"     # event
```

### 4. Inject into an event handler

```python
class PublishOrderPlacedHandler:
    def __init__(self, broker: InMemoryMessageBroker) -> None:
        self._broker = broker

    async def __call__(self, event: OrderPlaced) -> None:
        await self._broker.publish(
            "orders",
            OrderPlacedIntegration(
                order_id=str(event.order_id),
                customer_id=event.customer_id,
                total_amount=event.total_amount,
            ),
        )


broker = InMemoryMessageBroker()
handler = PublishOrderPlacedHandler(broker)

await handler(OrderPlaced(
    order_id=UUID("..."),
    customer_id="c1",
    total_amount=Decimal("99.99"),
))

assert len(broker.published) == 1
```

### 5. Pass to bootstrap

```python
from pydomain.infrastructure.bootstrap import bootstrap


broker = InMemoryMessageBroker()
app = await bootstrap(message_broker=broker)

app.message_bus.register_event(
    OrderPlaced,
    PublishOrderPlacedHandler(broker),
)
```

The `bootstrap()` function calls `broker.start()` (a no-op) — the same API as a production broker.

### 6. Verify ordering of published events

```python
async def test_publish_order():
    broker = InMemoryMessageBroker()

    await broker.publish("orders", event1)
    await broker.publish("orders", event2)
    await broker.publish("inventory", event3)

    # Order is preserved
    assert broker.published[0][1] == event1
    assert broker.published[1][1] == event2
    assert broker.published[2][1] == event3

    # Filter by topic
    order_events = [e for t, e in broker.published if t == "orders"]
    assert len(order_events) == 2
```

## Complete Example

```python
import pytest
from uuid import UUID
from decimal import Decimal

from pydomain.testing import InMemoryMessageBroker, FakeRepository, FakeUnitOfWork
from pydomain.infrastructure.bootstrap import bootstrap


class TestIntegrationEventPublishing:
    @pytest.fixture
    def broker(self) -> InMemoryMessageBroker:
        return InMemoryMessageBroker()

    @pytest.fixture
    async def app(self, broker):
        app = await bootstrap(message_broker=broker)
        app.message_bus.register_event(
            OrderPlaced,
            PublishOrderPlacedHandler(broker),
        )
        return app

    async def test_publishes_on_order_placed(self, app, broker):
        result, events = await app.dispatch(
            PlaceOrder(customer_id="c1", items=[OrderItem("widget", 2)])
        )

        assert len(broker.published) == 1
        topic, event = broker.published[0]
        assert topic == "orders"
        assert event.order_id is not None
        assert event.customer_id == "c1"
        assert event.total_amount > 0

    async def test_no_publish_when_handler_not_registered(self, broker):
        await broker.publish("test", DummyEvent())
        assert len(broker.published) == 1
        broker.published.clear()

        assert len(broker.published) == 0

    async def test_multiple_events_ordered(self, broker):
        e1 = OrderPlacedIntegration(order_id="1", customer_id="a", total_amount=10)
        e2 = OrderPlacedIntegration(order_id="2", customer_id="b", total_amount=20)
        e3 = OrderPlacedIntegration(order_id="3", customer_id="c", total_amount=30)

        await broker.publish("orders", e1)
        await broker.publish("orders", e2)
        await broker.publish("orders", e3)

        ids = [e.order_id for _, e in broker.published]
        assert ids == ["1", "2", "3"]

    async def test_start_and_stop_are_noops(self, broker):
        # These don't fail and don't change state
        await broker.start()
        await broker.stop()
        assert broker.published == []
```

## Expected Outcome

Your tests use `InMemoryMessageBroker` to capture integration events published by event handlers. The `published` list records every `(topic, event)` pair in order. No external broker is needed — tests run fast and deterministically.

## See Also

- [Message Broker concept](../../concepts/infrastructure/message-broker.md)
- [Configure a Message Broker](../infrastructure/configure-message-broker.md)
- [Implement an Integration Event](../cqrs/implement-integration-event.md)
- [Testing philosophy](../../concepts/testing/testing-philosophy.md)
