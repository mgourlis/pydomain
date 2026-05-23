# ADR-022: Integration Events — Primitive-Only Payloads

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

`IntegrationEvent` crosses service boundaries via message brokers (RabbitMQ, Kafka, etc.). It must be serializable to any broker format without custom logic. A message broker may use JSON, Avro, Protobuf, or a custom format.

## Decision

Restrict `IntegrationEvent` fields to primitive types: `str`, `int`, `float`, `bool`, `dict`, `list`, `None`. Enforce this via `@model_validator` at construction time.

Use `str` for `event_id` and `occurred_at` (instead of `UUID` and `datetime`).

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Allow complex types with custom serializers | Requires per-broker serialization logic; pushes complexity to users; fragile across broker formats |
| Broker-specific serialization adapters | Multiplies infrastructure code; each new broker needs a new adapter |

## Consequences

### Positive

- `model_dump()` always produces a broker-safe dict — no custom serialization needed.
- Early feedback: a developer who accidentally adds a `UUID` field gets an immediate error at construction, not a serialization failure in production.
- Universally compatible across languages and serialization formats.

### Negative

- Domain events and integration events have different type signatures for the same conceptual fields (e.g., `order_id: UUID` vs `order_id: str`).
- An explicit translation step is required: domain event → integration event.

### Neutral

- The translation step is intentional — it forces the application layer to define the public contract separately from the internal domain model.

## References

- §9.5 Integration Events — Primitive-Only Payloads
