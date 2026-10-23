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

    event_version: int = 1                             # Schema version of the payload

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
integration = OrderShippedIntegration(order_id="ORD-1", tracking="TRACK123")
stamped = integration.stamp_from(domain_event)
await broker.publish("orders.shipped", stamped)
```

Through the [outbox](../infrastructure/outbound-event-gateway.md) you do not stamp at all: the outbox entry records the domain event's `correlation_id` and its `event_id` as `causation_id`, and the relay stamps them onto the event it publishes, overriding any IDs the translator set, and passes them as `correlation_id` and `causation_id` message headers. For an unstamped domain event the relay does not stamp the integration event and passes no headers.

On the receiving side, the [`InboundEventGateway`](../infrastructure/inbound-event-gateway.md) automatically reads tracing IDs from the incoming integration event and stamps them onto the translated domain event — translators don't need to handle tracing at all.

## Primitive-Only Payload

Integration events enforce that every field is strict JSON — `str`, `int`, finite `float`, `bool`, `None`, and `dict` (with `str` keys) or `list` holding the same, at any depth:

```python
class OrderShippedIntegration(IntegrationEvent):
    order_id: str       # UUID as string
    customer_id: str    # UUID as string
    shipped_at: str     # ISO 8601
    carrier: str
    tracking_number: str
```

This constraint guarantees every integration event can be serialized to JSON by any message broker without custom serialization logic. The `@model_validator` enforces it at construction time, through dict values and list items: a `datetime` inside a `dict` field, a tuple, a set, a non-`str` dict key, NaN or infinity raises `ValueError` (wrapped in Pydantic's `ValidationError`), and so does a payload nested more than 100 levels deep or holding a cycle. The message names the field, the type and the path, never the value — for example `Field 'data' has disallowed type 'datetime' at data['at']`. The path includes dict keys, so do not put content in keys: ADR-071 does not protect them ([ADR-074](../../../adr/ADR-074-outbox-relay-and-registry-honour-their-contracts.md)).

## Publishing Integration Events

> **Prefer the outbox for anything that must not be lost.** An event handler runs after the commit, and after a command the event bus logs and swallows a handler's failure (ADR-046; a direct dispatch raises `EventHandlersFailed`, [ADR-081](../../../adr/ADR-081-handler-failures-reach-the-inbound-gateway.md)). So publishing from a handler is **best-effort**: a crash or a broker error between the commit and the publish loses the message. The [transactional outbox](../infrastructure/outbound-event-gateway.md) writes the integration event in the same transaction as the data and delivers it until it succeeds. Use a handler like the one below only where losing a message is acceptable.

An [Event Handler](handlers.md) can translate a domain event and publish the integration event directly:

```python
class PublishOrderShippedHandler:
    def __init__(self, broker: MessageBroker) -> None:
        self._broker = broker

    async def __call__(self, event: OrderShipped) -> None:
        integration_event = OrderShippedIntegration(  # the IntegrationEvent subclass, named apart from the domain event
            order_id=str(event.order_id),
            customer_id=str(event.customer_id),
            shipped_at=event.occurred_at.isoformat(),
            carrier=event.carrier,
            tracking_number=event.tracking_number,
        )
        await self._broker.publish("orders.shipped", integration_event)
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

**ACK/NACK semantics:** Invalid payloads (unknown topic, validation failure, translation error, a translator result that is not a `DomainEvent`) are acknowledged and discarded as poison messages. A handler failure reaches the gateway as `EventHandlersFailed` and propagates, so the subscriber negatively acknowledges and the broker retries ([ADR-081](../../../adr/ADR-081-handler-failures-reach-the-inbound-gateway.md)).

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
