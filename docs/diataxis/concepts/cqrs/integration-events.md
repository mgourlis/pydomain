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
| Tracing | `correlation_id`/`causation_id` (UUID) | `correlation_id`/`causation_id` (str) |

## The `IntegrationEvent` Base Class

```python
from pydantic import BaseModel, ConfigDict, Field
from pydomain.cqrs.integration_events import IntegrationEvent


class IntegrationEvent(BaseModel):
    event_id: str = Field(default_factory=...)        # UUIDv7 as string
    occurred_at: str = Field(default_factory=...)      # ISO 8601 UTC
    correlation_id: str | None = None                  # Business process ID
    causation_id: str | None = None                    # Direct cause ID

    model_config = ConfigDict(frozen=True)

    def stamp(self, *, correlation_id: str, causation_id: str) -> Self: ...
```

| Field | Type | Purpose |
|-------|------|---------|
| `event_id` | `str` | Unique event identifier (UUIDv7 as string) |
| `occurred_at` | `str` | ISO 8601 UTC timestamp |
| `correlation_id` | `str \| None` | Business process identifier — links all events in a workflow |
| `causation_id` | `str \| None` | ID of the event that directly caused this one — on the outbox path, the domain event's `event_id` |

All fields are strings (not UUID/datetime) to satisfy broker serialization without custom encoders. `correlation_id` and `causation_id` default to `None` — they are typically set via `stamp()` before publishing. `stamp()` validates the two IDs against their fields, so a value that is not a string raises Pydantic's `ValidationError` ([ADR-074](../../../adr/ADR-074-outbox-relay-and-registry-honour-their-contracts.md)).

## Distributed Tracing

Integration events carry the same tracing fields as domain events (`correlation_id` / `causation_id`), but as strings to satisfy the primitive-only constraint. This enables end-to-end workflow visibility across microservice boundaries:

```
Service A                                        Service B
─────────                                        ─────────
Command (correlation_id=cid1)
  → DomainEvent (cid1, event_id1)
    → IntegrationEvent.stamp(cid1, event_id1)
      → Broker ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ → Gateway
                                          → DomainEvent (cid1, event_id1)
                                            → Saga continues chain
```

The `stamp_from()` convenience method copies tracing IDs from a domain event in one call:

```python
integration = OrderShipped(order_id="ORD-1", tracking="TRACK123")
stamped = integration.stamp_from(domain_event)
await broker.publish("orders.shipped", stamped)
```

Through the [outbox](../infrastructure/outbound-event-gateway.md) you do not stamp at all: the outbox entry records the domain event's `correlation_id` and its `event_id` as `causation_id`, and the relay stamps them onto the event it publishes, overriding any IDs the translator set, and passes them as `correlation_id` and `causation_id` message headers. An unstamped domain event produces an unstamped integration event.

On the receiving side, the [`InboundEventGateway`](../infrastructure/inbound-event-gateway.md) automatically reads tracing IDs from the incoming integration event and stamps them onto the translated domain event — translators don't need to handle tracing at all.

## Primitive-Only Payload

Integration events enforce that every field is strict JSON — `str`, `int`, finite `float`, `bool`, `None`, and `dict` (with `str` keys) or `list` holding the same, at any depth:

```python
class OrderShipped(IntegrationEvent):
    order_id: str       # UUID as string
    customer_id: str    # UUID as string
    shipped_at: str     # ISO 8601
    carrier: str
    tracking_number: str
```

This constraint guarantees every integration event can be serialized to JSON by any message broker without custom serialization logic. The `@model_validator` enforces it at construction time, through dict values and list items: a `datetime` inside a `dict` field, a tuple, a set, a non-`str` dict key, NaN or infinity raises `ValueError` (wrapped in Pydantic's `ValidationError`). The message names the field, the type and the path, never the value — for example `Field 'data' has disallowed type 'datetime' at data['at']` ([ADR-074](../../../adr/ADR-074-outbox-relay-and-registry-honour-their-contracts.md)).

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
