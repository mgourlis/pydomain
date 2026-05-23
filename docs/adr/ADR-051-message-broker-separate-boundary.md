# ADR-051: `MessageBroker` Protocol — Separate Boundary from MessageBus

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

The `MessageBus` dispatches domain events to local handlers (in-process). But integration events must be published to external message brokers (RabbitMQ, Kafka, etc.) for cross-service communication.

These are fundamentally different concerns:
- **MessageBus**: In-process event dispatch. Synchronous (sequential handlers). Failure isolation per handler.
- **MessageBroker**: Cross-process event publishing. Asynchronous. Requires connection management, retries, and serialization.

Mixing broker publishing into the `MessageBus` would couple the in-process dispatch to external infrastructure.

## Decision

`MessageBroker` is a separate Protocol in the infrastructure layer:

```python
@runtime_checkable
class MessageBroker(Protocol):
    async def publish(self, topic: str, event: IntegrationEvent) -> None:
        """Publish an integration event to the given topic."""

    async def start(self) -> None:
        """Initialize connection or resources. Called at application startup."""

    async def stop(self) -> None:
        """Graceful shutdown and resource cleanup. Called at application shutdown."""
```

**Key properties**:
- **`IntegrationEvent` only**: The broker publishes `IntegrationEvent` instances (primitive payloads per ADR-022), not `DomainEvent` instances.
- **Topic-based**: Each event is published to a named topic/routing key.
- **Lifecycle methods**: `start()` and `stop()` manage connection lifecycle.
- **`runtime_checkable`**: Tests can use `isinstance()` checks.

**Integration with bootstrap**:

```python
async def bootstrap(..., message_broker=None):
    if message_broker is not None:
        await message_broker.start()
```

The broker is started during bootstrap and stopped during application shutdown.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Merge broker into MessageBus | Couples in-process dispatch to external infrastructure; harder to test |
| No broker Protocol (direct RabbitMQ/Kafka usage) | No abstraction; cannot swap implementations; hard to test |
| Abstract base class (not Protocol) | Requires inheritance; Protocol is more Pythonic for structural typing |

## Consequences

### Positive

- Clean separation: `MessageBus` (local) vs `MessageBroker` (external).
- Broker is a Protocol — any implementation (RabbitMQ, Kafka, in-memory test double) satisfies it.
- `start()`/`stop()` lifecycle is managed by `bootstrap()`.
- Tests can inject an `InMemoryMessageBroker` to capture published events.

### Negative

- Two messaging abstractions to learn (MessageBus and MessageBroker).
- The broker does not participate in the Unit of Work — publishing is fire-and-forget.

### Neutral

- The broker lives in `infrastructure/` while the bus also lives in `infrastructure/` — both are infrastructure concerns but at different scales.

## References

- `src/pydomain/infrastructure/message_broker.py` — `MessageBroker` Protocol
- `src/pydomain/cqrs/integration_events.py` — `IntegrationEvent` class
- `src/pydomain/infrastructure/bootstrap.py` — `bootstrap()` starts the broker
- ADR-022: Integration Events — Primitive-Only Payloads
