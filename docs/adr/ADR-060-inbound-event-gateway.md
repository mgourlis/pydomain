# ADR-060: `InboundEventGateway` — Bridging External Brokers to the Internal MessageBus

## Status

Accepted

## Date

2026-05-22

## Context

Integration events arriving from external message brokers need to enter the domain's event
processing pipeline. The raw JSON payloads must be:

1. **Hydrated** into typed `IntegrationEvent` instances.
2. **Translated** to `DomainEvent` instances via an Anti-Corruption Layer (converting
   primitive types like `str` to rich domain types like `UUID`).
3. **Dispatched** into the `MessageBus` for internal routing to event handlers.

These three steps form a gateway that bridges the external messaging world (primitive types,
topic routing) to the internal domain world (rich types, event handlers). Without a
dedicated gateway, this translation logic would be scattered across handlers or embedded in
concrete subscriber implementations.

Additionally, the two failure modes — bad payloads vs. dispatch failures — have different
recovery semantics for message acknowledgment:

- A malformed message will never succeed (poison message) → should be acknowledged and
  discarded.
- A handler failure may be transient → should be negatively acknowledged for retry.

## Decision

Define `InboundEventGateway` that bridges a `MessageSubscriber` to a `MessageBus`:

```python
class InboundEventGateway:
    def __init__(self, subscriber: MessageSubscriber, message_bus: MessageBus) -> None:
        self._subscriber = subscriber
        self._message_bus = message_bus
        self._registry: dict[str, tuple[type[IntegrationEvent], Callable]] = {}

    def register_translation[T: IntegrationEvent](
        self,
        topic: str,
        integration_class: type[T],
        translator: Callable[[T], DomainEvent],
    ) -> None: ...

    async def _process_message(self, topic: str, payload: dict[str, Any]) -> None:
        ...
```

**Processing pipeline (`_process_message`):**

1. **Lookup**: Resolve the integration class and translator from `_registry` by topic.
   Unknown topics are logged as warnings and discarded.
2. **Hydrate**: Validate and construct the `IntegrationEvent` via
   `integration_class.model_validate(payload)`. Validation errors are logged and discarded.
3. **Translate**: Pass the hydrated event through the translator (Anti-Corruption Layer).
   Translation errors are logged and discarded.
4. **Dispatch**: Call `self._message_bus.dispatch(domain_event)`. Dispatch errors
   propagate (not caught) so the subscriber can NACK the message.

**ACK/NACK convention:**

| Failure mode | Behaviour | Recovery |
|---|---|---|
| Unknown topic | Log warning, return | ACK (poison) |
| Validation error | Log error, return | ACK (poison) |
| Translation error | Log error, return | ACK (poison) |
| Dispatch failure | Log error, re-raise | NACK (retry) |

**Flat payload pattern**: The gateway uses no `EventRegistry` or envelope wrapper. The type
is implied by the topic — `register_translation` maps a topic string directly to an
integration class.

**Sync registration**: `register_translation` is synchronous — it performs no I/O, only
registry update and subscription wiring. The actual async handler is an inner closure.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| EventRegistry for type resolution | Introduces infrastructure with no benefit — type resolution via `model_validate` is immediate and unambiguous when the topic implies the type. |
| Envelope-based dispatch | Adds indirection. The flat pattern (topic → type) is simpler and already established by ADR-022. |
| Translation in the MessageSubscriber | Couples the subscriber to domain types. The subscriber should remain broker-agnostic and deliver raw dicts only. |
| Gateway passes raw dicts to a user-written handler | Just moves the translation burden to users, defeating the purpose of a gateway. |

## Consequences

### Positive

- Clear ACK/NACK boundary — poison messages are always discarded; transient failures always propagate.
- Translation code is isolated in one place — the Anti-Corruption Layer is explicit, not scattered.
- Subscriber implementations remain domain-ignorant — they deal only with `dict[str, Any]`.
- Same-topic re-registration overwrites the previous mapping, enabling hot-swap of integration event versions without subscriber restart.

### Negative

- Gateway adds one hop and one abstraction to the inbound path.
- The `register_translation` closure captures `topic` and `self` — developers must ensure the gateway outlives its usage.
- Dispatch failures propagate through the subscriber's handler — the concrete subscriber must implement ACK/NACK logic (some brokers may not support NACK).

### Neutral

- Re-registration is intentional — enabling schema evolution without restart. The overwrite semantics are documented and tested.

## References

- `src/pydomain/infrastructure/message_subscriber.py` — `InboundEventGateway` class (lines 121–250)
- `tests/infrastructure/test_inbound_event_gateway.py` — 10 test methods covering happy path, validation, translation, dispatch, lifecycle, multi-topic, extra fields, re-registration
- ADR-058: MessageBus Dispatch Extended for DomainEvent
- ADR-059: `MessageSubscriber` Protocol
- ADR-051: `MessageBroker` Protocol
- ADR-022: Integration Events — Primitive-Only Payloads
