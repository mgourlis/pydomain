# Integration Events

> **Adoption Level:** 3 — CQRS with External Systems
> **Module:** `pydomain.cqrs.integration_events`

## What are Integration Events?

**Integration Events** are the cross-boundary counterpart to [Domain Events](../ddd/domain-events.md). While domain events circulate **within** a bounded context, integration events cross **between** bounded contexts or to external systems via a Message Broker.

## Domain Events vs Integration Events

| Aspect | Domain Event | Integration Event |
|--------|-------------|-------------------|
| Scope | Within bounded context | Across bounded contexts |
| Payload | Domain objects allowed | Primitives only |
| Transport | In-memory MessageBus | Message Broker (RabbitMQ, Kafka) |
| Serialization | Python objects | JSON (broker-compatible) |
| ID type | `UUID` | `str` |
| Timestamp | `datetime` | `str` (ISO 8601) |

## The `IntegrationEvent` Base Class

```python
from pydantic import BaseModel, ConfigDict, Field
from pydomain.cqrs.integration_events import IntegrationEvent


class IntegrationEvent(BaseModel):
    event_id: str = Field(default_factory=...)     # UUIDv7 as string
    occurred_at: str = Field(default_factory=...)   # ISO 8601 UTC

    model_config = ConfigDict(frozen=True)
```

| Field | Type | Purpose |
|-------|------|---------|
| `event_id` | `str` | Unique event identifier (UUIDv7 as string) |
| `occurred_at` | `str` | ISO 8601 UTC timestamp |

Both fields are strings (not UUID/datetime) to satisfy broker serialization without custom encoders.

## Primitive-Only Payload

Integration events enforce that all fields are primitives — `str`, `int`, `float`, `bool`, `dict`, `list`, `None`:

```python
class OrderShipped(IntegrationEvent):
    order_id: str       # UUID as string
    customer_id: str    # UUID as string
    shipped_at: str     # ISO 8601
    carrier: str
    tracking_number: str
```

This constraint guarantees every integration event can be serialized to JSON by any message broker without custom serialization logic. The `@model_validator` enforces this at construction time.

## Publishing Integration Events

Integration events are typically published from an [Event Handler](handlers.md) that translates a domain event:

```python
class PublishOrderShippedHandler:
    def __init__(self, broker: MessageBroker) -> None:
        self._broker = broker

    async def __call__(self, event: OrderShipped) -> None:
        integration_event = OrderShippedIntegrationEvent(
            order_id=str(event.order_id),
            customer_id=str(event.customer_id),
            shipped_at=event.occurred_at.isoformat(),
            carrier=event.carrier,
            tracking_number=event.tracking_number,
        )
        await self._broker.publish(integration_event)
```

This is a one-way translation: domain event → integration event → broker.

## Receiving Integration Events

Integration events that arrive from external brokers enter the application through the inbound pipeline:

```
[External broker] → MessageSubscriber.subscribe()
                    ↓  (raw dict payload)
                    InboundEventGateway._process_message()
                    ↓  (hydrate → validate → translate)
                    MessageBus.dispatch(domain_event)
                    ↓  (route to event handlers)
                    [Internal domain handlers]
```

The [`InboundEventGateway`](../infrastructure/inbound-event-gateway.md) bridges the `MessageSubscriber` (which delivers raw JSON payloads) to the `MessageBus` (which routes typed `DomainEvent` instances). The translation step converts primitive-typed integration event fields to rich domain types:

```python
gateway.register_translation(
    topic="orders",
    integration_class=OrderPlacedIntegration,
    translator=lambda e: OrderPlaced(
        order_id=UUID(e.order_id),
        customer_id=UUID(e.customer_id),
        total_amount=Decimal(e.total_amount),
    ),
)
```

**ACK/NACK semantics:** Invalid payloads (unknown topic, validation failure, translation error) are acknowledged and discarded as poison messages. Dispatch failures propagate so the subscriber can negatively acknowledge and retry.

## When to Use Integration Events

Use integration events when:
- Communicating between bounded contexts (e.g., Orders → Shipping)
- Notifying external systems of domain state changes
- Building event-driven architectures across services

Use domain events when:
- Communicating within the same bounded context
- Reacting to aggregate state changes in-process

## Next Steps

- **[Implement an Integration Event →](../../how-to/cqrs/implement-integration-event.md)** — step-by-step guide
- **[Domain Events →](../ddd/domain-events.md)** — the in-process counterpart
- **[Message Broker →](../infrastructure/message-broker.md)** — broker configuration
- **[MessageSubscriber →](../infrastructure/message-subscriber.md)** — receiving integration events
- **[InboundEventGateway →](../infrastructure/inbound-event-gateway.md)** — bridging external brokers to the internal bus
- **[Publish Integration Events (Recipe) →](../../how-to/recipes/publish-integration-events.md)** — end-to-end pattern
