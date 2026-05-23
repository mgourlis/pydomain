# ADR-059: `MessageSubscriber` Protocol — Subscriber-Side Counterpart to `MessageBroker`

## Status

Accepted

## Date

2026-05-22

## Context

ADR-051 defined `MessageBroker` as the publish-side Protocol for sending `IntegrationEvent`
instances to external brokers. A subscriber-side counterpart is required for receiving
integration events from those same brokers (Kafka, RabbitMQ, etc.).

The subscriber differs from the broker in key ways:

- **Broker**: publish-only (`publish(topic, event)`). One-shot, no persistent subscription.
- **Subscriber**: receive-only (`subscribe(topic, handler)`). Maintains a subscription
  registry, manages consumer group state, and acknowledges/rejects messages.

A dedicated Protocol is needed to decouple the application from concrete broker
implementations on the receiving side.

## Decision

Define `MessageSubscriber` as a `@runtime_checkable` Protocol in the infrastructure layer:

```python
@runtime_checkable
class MessageSubscriber(Protocol):
    def subscribe(
        self, topic: str, handler: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...
```

**Key properties:**

- **`subscribe` is sync** — its only purpose is registration (mapping topics to async
  callables). No I/O occurs during registration.
- **Handler receives `dict[str, Any]`** — raw JSON payloads. Type resolution and hydration
  are delegated to the `InboundEventGateway`. This maintains the flat-payload pattern
  (the topic implies the type, not an envelope wrapper).
- **Async lifecycle** — `start()` and `stop()` manage connection and consumer group
  lifecycle. These are async because they involve I/O (connecting, joining consumer groups,
  committing offsets).
- **`@runtime_checkable`** — consistent with all other Protocols in the library. Tests can
  use `isinstance()` to verify conformance.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Async `subscribe` | Registration is pure mapping of topic to handler — no I/O. Making it async suggests it performs network operations, which is misleading. |
| Single `MessageBroker` with publish and subscribe | Publish and subscribe are independent concerns with different lifecycle and error semantics. Combining them would force implementations to handle both sides even when only one is needed. |
| Envelope-based dispatch (handler receives `{topic, type, payload}`) | Flat payload pattern is already established (ADR-022). The topic provides the type information; an envelope adds indirection without value. |

## Consequences

### Positive

- Clean publish/subscribe separation — `MessageBroker` for outbound, `MessageSubscriber` for inbound.
- Sync `subscribe` makes registration simple and testable — no async plumbing needed for setup.
- Consumer implementations are free to handle ACK/NACK semantics according to their broker's protocol.

### Negative

- The subscriber cannot participate in the handler's return type — the handler's `None` return means ACK/NACK decisions must be managed internally by the concrete implementation.

### Neutral

- The subscriber delivers raw dicts — the `InboundEventGateway` owns type resolution. This is an explicit design choice, not a limitation.

## References

- `src/pydomain/infrastructure/message_subscriber.py` — `MessageSubscriber` Protocol (lines 83–118)
- `tests/infrastructure/test_message_subscriber.py` — Protocol conformance tests
- ADR-051: `MessageBroker` Protocol — separate boundary from MessageBus
- ADR-022: Integration Events — Primitive-Only Payloads
