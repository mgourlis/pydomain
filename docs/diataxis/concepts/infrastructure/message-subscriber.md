# MessageSubscriber Protocol

> **Adoption Level:** 3 — CQRS with Events
> **Module:** `pydomain.infrastructure.message_subscriber`

## What is the MessageSubscriber?

The **MessageSubscriber** is a `Protocol` that defines the contract for receiving integration events from external message brokers (Kafka, RabbitMQ, Redis). It is the **inbound** counterpart to the [`MessageBroker`](message-broker.md) protocol, which handles outbound publishing.

A subscriber delivers raw JSON dict payloads to registered handlers. Type resolution and hydration into typed `IntegrationEvent` instances is handled by the [`InboundEventGateway`](inbound-event-gateway.md) that wraps it.

## Why It Exists

The flow is symmetric:

```
Outbound:  DomainEvent → IntegrationEvent → MessageBroker.publish() → external broker
Inbound:   external broker → MessageSubscriber → InboundEventGateway → MessageBus.dispatch()
                                                                          ↓
                                                                   DomainEvent
```

Without the `MessageSubscriber` protocol, every inbound integration would be coupled to a specific broker library (aio_pika for RabbitMQ, aiokafka for Kafka, etc.). The protocol decouples the gateway from the transport — swap the subscriber implementation without touching the translation logic.

## The Protocol

```python
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MessageSubscriber(Protocol):
    def subscribe(
        self, topic: str, handler: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None: ...

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
```

Three methods, no dependencies on any specific broker library.

### `subscribe(topic, handler)`

Register an async handler for messages arriving on a topic:

```python
async def handle_payload(payload: dict[str, Any]) -> None:
    # payload is raw JSON dict
    print(f"Received on orders: {payload}")

subscriber.subscribe("orders", handle_payload)
```

The handler receives the raw JSON dict. The subscriber maps `topic` to the transport-specific concept (Kafka topic, RabbitMQ routing key, Redis channel) internally.

### `start()`

Begin consuming messages. Called once at application startup:

```python
await subscriber.start()
```

### `stop()`

Graceful shutdown. Called at application shutdown to close connections and stop consumers:

```python
await subscriber.stop()
```

## Runtime Checkable

The `@runtime_checkable` decorator means you can use `isinstance` checks:

```python
from pydomain.infrastructure.message_subscriber import MessageSubscriber

assert isinstance(kafka_subscriber, MessageSubscriber)  # True
assert isinstance(object(), MessageSubscriber)            # False
```

Any class with `subscribe`, `start`, and `stop` methods satisfies the protocol — no need to inherit or register.

## Relationship to InboundEventGateway

The `MessageSubscriber` alone delivers raw dicts. The [`InboundEventGateway`](inbound-event-gateway.md) sits on top of it to:

1. **Hydrate** the raw dict into a typed `IntegrationEvent` via Pydantic `model_validate`
2. **Translate** the integration event to a `DomainEvent` via an Anti-Corruption Layer translator
3. **Dispatch** the domain event into the `MessageBus` for internal routing

The gateway calls `subscriber.subscribe()` internally when you register a translation — you never call `subscribe()` directly when using the gateway.

## Failure Mode Contract

The subscriber protocol defines two failure modes:

| Failure | Behavior | Subscriber Action |
|---------|----------|-------------------|
| Validation/translation failure | Logged, exception swallowed | **ACK** — poison message, will never succeed |
| Dispatch failure | Exception propagates | **NACK** — retryable, handler may recover |

The concrete subscriber implementation decides how to ACK/NACK based on whether the gateway raises or swallows the exception.

## Transport Implementations

| Transport | Notes |
|-----------|-------|
| In-Memory | `FakeMessageSubscriber` — testing only (ships in `tests/`) |
| Kafka | Implement `subscribe` → consumer, `start` → poll loop, `stop` → close consumer |
| RabbitMQ | Implement `subscribe` → bind queue, `start` → consume, `stop` → close channel |
| Redis Pub/Sub | Implement `subscribe` → subscribe, `start` → listen, `stop` → unsubscribe |

Production implementations are application-specific; the project provides the protocol and the gateway.

## Design Decision

The `MessageSubscriber` protocol was introduced alongside the `MessageBroker` protocol to complete the inbound/outbound symmetry. Previously, only outbound publishing was abstracted — inbound receiving required direct broker coupling. The split is:

- `MessageBroker` — **outbound** (this app publishes to external services)
- `MessageSubscriber` — **inbound** (this app receives from external services)
- `InboundEventGateway` — the **bridge** that translates external events into internal domain events and dispatches them

## Next Steps

- **[InboundEventGateway →](inbound-event-gateway.md)** — how the gateway bridges external brokers to the internal bus
- **[Configure a MessageSubscriber →](../../how-to/infrastructure/configure-message-subscriber.md)** — step-by-step subscriber implementation
- **[Configure an InboundEventGateway →](../../how-to/infrastructure/configure-inbound-event-gateway.md)** — wiring the gateway
- **[Message Broker →](message-broker.md)** — the outbound counterpart
- **[Application Bootstrap →](bootstrap.md)** — where the gateway is wired
- **[Integration Events →](../cqrs/integration-events.md)** — integration event concepts
