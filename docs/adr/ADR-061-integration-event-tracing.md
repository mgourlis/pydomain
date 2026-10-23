# ADR-061: Integration Event Distributed Tracing — `correlation_id` / `causation_id`

## Status

Accepted

## Date

2026-05-30

## Context

When a workflow spans multiple microservices — Order Service → Payment Service →
Shipping Service — the trace chain must survive the broker boundary.  Without
tracing metadata on integration events, each service sees an isolated event with
no knowledge of the originating business process.

The library already has a mature internal tracing infrastructure:

- `DomainEvent` carries `correlation_id` and `causation_id` (ADR-011, ADR-021)
- The `CommandBus` propagates tracing from commands through the UoW to domain events
- `SagaManager` propagates tracing across saga steps (ADR-032)
- `MessageContext` flows tracing through every pipeline behaviour

But at the service boundary — `MessageBroker.publish()` → broker → `InboundEventGateway` —
tracing was severed.  `IntegrationEvent` had only `event_id` and `occurred_at`.
No `correlation_id`, no `causation_id`.

## Decision

Add `correlation_id` and `causation_id` fields directly to `IntegrationEvent`,
mirroring the proven `DomainEvent.stamp()` pattern:

```python
class IntegrationEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid7()))
    occurred_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    correlation_id: str | None = None    # ← new
    causation_id: str | None = None      # ← new

    def stamp(self, *, correlation_id: str, causation_id: str) -> Self:
        return self.model_copy(update={
            "correlation_id": correlation_id,
            "causation_id": causation_id,
        })
```

> **Amended by [ADR-074](ADR-074-outbox-relay-and-registry-honour-their-contracts.md).** `stamp()` now validates the two ids against their fields, and a value that is not a string raises Pydantic's `ValidationError`, as `DomainEvent.stamp()` does (ADR-070).

**Publishing side** — the publisher stamps the integration event before publishing:

```python
integration = OrderShipped(order_id="ORD-1", tracking="TRACK123")
stamped = integration.stamp(
    correlation_id=str(domain_event.correlation_id),
    causation_id=str(domain_event.event_id),
)
await broker.publish("orders.shipped", stamped)
```

> **Amended by [ADR-074](ADR-074-outbox-relay-and-registry-honour-their-contracts.md).** On the outbox path the publisher is the relay: an outbox entry records the domain event's `correlation_id` and its `event_id` as `causation_id` (both `None` for an unstamped event), and the relay stamps them onto the integration event, overriding the translator's, and passes them as `headers={"correlation_id": ..., "causation_id": ...}`.

**Consuming side** — `InboundEventGateway._process_message()` auto-reads tracing
IDs from the integration event and stamps them onto the translated `DomainEvent`
via `DomainEvent.stamp()`. Translators (ACL) don't need to know about tracing at all:

```python
integration_event = integration_class.model_validate(payload)
domain_event = translator(integration_event)
if integration_event.correlation_id and integration_event.causation_id:
    domain_event = domain_event.stamp(
        correlation_id=UUID(integration_event.correlation_id),
        causation_id=UUID(integration_event.causation_id),
    )
```

**No envelope, no `data` nesting, no type discriminator.**  The tracing fields live
on the integration event itself.  The flat JSON payload carries everything.

**Backward compatibility:** events without tracing (`correlation_id=None`,
`causation_id=None`) continue to work unchanged.  The gateway gracefully skips
stamping when IDs are absent or malformed.

## Alternatives Considered

| Alternative | Rejection Reason |
|---|---|
| `IntegrationEventEnvelope` wrapper class (`{"message_id", "correlation_id", "data": {...}}`) | Adds a second class with redundant identity. `message_id` competes with `event_id`. `data` nesting adds indirection. The `DomainEvent.stamp()` pattern is already proven in this codebase. |
| Envelope inheriting from `IntegrationEvent` | Envelope's `event_id` would be the transmission identity, inner `data.event_id` the business identity — duplicated, confusing. Two `occurred_at` values is noise. |
| CloudEvents-style envelope (`id`, `type`, `source`, `data`, `time` + extensions) | Over-engineered for the library's scope. The flat payload with optional tracing IDs achieves the same result with zero new abstractions. |
| Use `EventRegistry` for integration event type resolution (`{"type": ..., "data": ...}`) | Solves a problem integration events don't have. Domain events need type discriminators because the event store mixes them; integration events have dedicated topics — the topic IS the discriminator. |
| No tracing on integration events (status quo) | The library claims to support CQRS/ES across microservices but can't trace workflows across them. |

## Consequences

### Positive

- End-to-end traceability across microservice boundaries: `user action → command → domain event → integration event → broker → gateway → domain event → saga → ...`
- Consistent pattern with `DomainEvent.stamp()` — one mental model for both event types
- `InboundEventGateway` auto-stamps domain events — translators stay clean (pure ACL)
- Zero new classes, zero new abstractions, zero protocol signature changes
- Backward compatible — unstamped events still dispatch correctly (tracing = None)

### Negative

- `correlation_id`/`causation_id` are `str` (not `UUID`) to satisfy the primitive-only constraint. Publishers convert `UUID → str` when stamping; the gateway converts back `str → UUID` when stamping domain events. This is a thin conversion layer at the boundary.

### Neutral

- Both `IntegrationEvent.stamp()` and `DomainEvent.stamp()` return `Self` for correct subclass type inference. (Updated in the same change for consistency.)
- The `stamp_from(domain_event)` convenience reduces the publisher's ceremony to a single call — no need to manually convert UUIDs to strings.
- **Idempotency:** The gateway sets the domain event's `causation_id` to the integration event's own `event_id` (not its `causation_id`). This means the integration event IS the direct cause in the consumer's trace chain, and broker redelivery of the same integration event produces domain events with the same `causation_id` — consumers can use this for deduplication.

> **Note from [ADR-080](ADR-080-identify-delivered-events-for-event-idempotency.md) points 1 and 9.** Takes effect with DCE-131 (W2-2). `causation_id` is tracing only; consumers deduplicate on the domain event's `event_id`, which the gateway derives from the integration event's `event_id` unless the translator set one.

## References

- `src/pydomain/cqrs/integration_events.py` — `IntegrationEvent.correlation_id`, `.causation_id`, `.stamp()`, `.stamp_from()`
- `src/pydomain/infrastructure/message_subscriber.py` — `InboundEventGateway._process_message()` tracing propagation and idempotency
- `src/pydomain/ddd/domain_event.py` — `DomainEvent.stamp()` (returns `Self`, consistent with `IntegrationEvent.stamp()`)
- ADR-011: Domain Event Stamp Immutability
- ADR-021: Correlation/Causation Propagation via UoW Stamping
- ADR-032: Saga Correlation via `event.correlation_id`
- ADR-060: InboundEventGateway — Bridging External Brokers to the Internal MessageBus
- ADR-022: Integration Events — Primitive-Only Payloads
