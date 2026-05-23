# Message Broker

> **Adoption Level:** 3 — CQRS with Events
> **Module:** `pydomain.infrastructure.message_broker`

## What is the Message Broker?

The **Message Broker** is a protocol for publishing integration events across service boundaries. It decouples the publisher (this application) from the transport (RabbitMQ, Kafka, Redis, SQS) — code against the protocol, swap the implementation.

It is distinct from the [Message Bus](message-bus.md), which handles *in-process* dispatch of commands, queries, and events. The Message Broker handles *cross-process* messaging.

## Why It Exists

Domain events are in-process. Integration events are cross-process. Something needs to bridge the gap. The `MessageBroker` protocol defines the **outbound** bridge (publishing), while its counterpart [`MessageSubscriber`](message-subscriber.md) defines the **inbound** bridge (receiving):

```
Outbound (publish):
  [Command Handler] → aggregate method → domain event
                      ↓
  [MessageBus] → event handlers → integration event → MessageBroker.publish()
                                                          ↓
                                                    [External services]

Inbound (receive):
  [External broker] → MessageSubscriber → InboundEventGateway
                                            ↓  (hydrate → translate → dispatch)
                                        MessageBus.dispatch(domain_event)
```

## The `MessageBroker` Protocol

```python
from pydomain.infrastructure.message_broker import MessageBroker
from pydomain.cqrs.integration_events import IntegrationEvent


@runtime_checkable
class MessageBroker(Protocol):
    async def publish(self, topic: str, event: IntegrationEvent) -> None: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
```

Three methods, no dependencies on any specific broker library.

### `publish(topic, event)`

Publish an integration event to a named topic:

```python
await broker.publish(
    topic="orders",
    event=OrderPlacedIntegration(
        order_id="o1",
        customer_id="c1",
        total_amount=Decimal("99.99"),
    ),
)
```

The `topic` is a logical name, not a transport-specific address. The implementation maps it to the underlying queue/exchange/stream name.

### `start()`

Initialize the connection. Called once during bootstrap:

```python
await broker.start()  # Connect to RabbitMQ / Kafka / etc.
```

### `stop()`

Gracefully close the connection. Called during shutdown:

```python
await broker.stop()  # Close channels, flush buffers
```

## Integration with Bootstrap

If you pass a `MessageBroker` to `bootstrap()`, it calls `start()` for you:

```python
from pydomain.infrastructure.bootstrap import bootstrap


broker = RabbitMQBroker(connection_url="amqp://localhost")

app = await bootstrap(message_broker=broker)
# → broker.start() called inside bootstrap()
```

The broker is not exposed on `Application` directly — it's typically injected into event handlers that need it:

```python
class PublishOrderPlacedHandler:
    def __init__(self, broker: MessageBroker) -> None:
        self._broker = broker

    async def __call__(self, event: OrderPlaced) -> None:
        await self._broker.publish("orders", OrderPlacedIntegration(
            order_id=event.order_id,
            customer_id=event.customer_id,
        ))
```

## The Publish Pattern

The idiomatic flow: domain event → event handler → integration event → broker:

```
1. Command handler calls aggregate.place()
2. Aggregate records OrderPlaced domain event
3. CommandBus commits UoW, collects events
4. MessageBus dispatches OrderPlaced to handlers:
   ├── PublishOrderPlacedHandler → broker.publish("orders", integration_event)
   ├── SendConfirmationHandler → email
   └── UpdateProjectionHandler → read store
```

The integration event is a *separate type* from the domain event. The domain event carries internal state; the integration event carries the public API contract:

```python
# Domain event (internal — may change)
class OrderPlaced(DomainEvent):
    order_id: str
    customer_id: str
    internal_pricing_tier: str  # internal concern

# Integration event (external — stable contract)
class OrderPlacedIntegration(IntegrationEvent):
    order_id: str
    customer_id: str
    total_amount: Decimal
    items: list[OrderItemData]
```

This decoupling means you can evolve your domain model without breaking downstream consumers.

## In-Memory Fake for Testing

```python
from pydomain.testing.in_memory_message_broker import InMemoryMessageBroker


broker = InMemoryMessageBroker()
await broker.start()
await broker.publish("orders", event)

# Assert what was published
published = broker.published_messages
assert published[0].topic == "orders"
assert published[0].event.order_id == "o1"
```

The in-memory fake records all published messages for test assertions. See [In-Memory Message Broker →](../../how-to/testing/use-in-memory-message-broker.md).

## Transport Implementations

The `MessageBroker` protocol can be implemented for any transport:

| Transport | Implementation | Notes |
|-----------|---------------|-------|
| In-Memory | `InMemoryMessageBroker` | Testing only |
| RabbitMQ | Custom (implement protocol) | Exchange + routing key |
| Kafka | Custom (implement protocol) | Topic + partition key |
| Redis Pub/Sub | Custom (implement protocol) | Channel publish |
| AWS SQS/SNS | Custom (implement protocol) | Queue/topic publish |

The project ships with the in-memory fake. Production implementations are application-specific.

## Next Steps

- **[Configure a Message Broker →](../../how-to/infrastructure/configure-message-broker.md)** — wiring a broker implementation
- **[Implement an Integration Event →](../../how-to/cqrs/implement-integration-event.md)** — creating integration events
- **[Handle Domain Events →](../../how-to/cqrs/handle-domain-events.md)** — the event handler that publishes
- **[Application Bootstrap →](bootstrap.md)** — where the broker is wired
- **[Integration Events →](../cqrs/integration-events.md)** — integration event concepts
- **[MessageSubscriber Protocol →](message-subscriber.md)** — receiving integration events
- **[InboundEventGateway →](inbound-event-gateway.md)** — bridging external brokers to the internal bus
