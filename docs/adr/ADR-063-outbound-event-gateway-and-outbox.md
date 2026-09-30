# ADR-063: Outbound Event Gateway and Transactional Outbox

## Status

Accepted

## Date

2026-09-29

## Context

pydomain implements the inbound messaging path completely: `MessageSubscriber` → `InboundEventGateway` → `MessageBus`, with hydration, Anti-Corruption Layer translation, tracing propagation, and a documented ACK/NACK convention (ADR-059, ADR-060).

The outbound path has no implementation. `MessageBroker` is defined as a Protocol (ADR-051) and its lifecycle is managed by `bootstrap()` (ADR-047), but `publish()` has **no call site anywhere in the library**. `AbstractUnitOfWork._write_outbox()` is documented as an extension point and is a no-op, overridden nowhere.

ADR-051 records the consequence honestly under its own Negative section:

> "The broker does not participate in the Unit of Work — publishing is fire-and-forget."

That is the gap this ADR closes. Three forces shape the solution:

1. **Atomicity.** An integration event must not be published for uncommitted state, and committed state must not go unpublished because the broker was down at commit time.
2. **The type boundary.** The UoW collects `DomainEvent`s (rich types: `UUID`, `datetime`). The broker carries `IntegrationEvent`s (primitives only, ADR-022). The two meet only through an explicit translator — the Anti-Corruption Layer.
3. **Consistency with ADR-060.** The library already decided how gateways resolve types: *"EventRegistry for type resolution — Introduces infrastructure with no benefit — type resolution via `model_validate` is immediate and unambiguous when the topic implies the type."* Any outbound design that depends on `EventRegistry` contradicts an accepted decision.

## Decision

We will introduce a transactional outbox and an outbound gateway, with translation performed at **write** time.

**1. `OutboxEntry` — the durable record.** A frozen Pydantic model carrying `message_id`, `topic`, `payload` (the serialized integration event), `event_version`, `occurred_at`, `correlation_id`, and `causation_id`.

**2. `OutboxStore` — a minimal persistence port.** A `@runtime_checkable` Protocol of exactly four methods:

```python
async def append(self, entries: Sequence[OutboxEntry]) -> None: ...
async def fetch_unpublished(self, limit: int) -> list[OutboxEntry]: ...
async def mark_published(self, message_ids: Sequence[UUID]) -> None: ...
async def mark_failed(self, message_id: UUID, *, error: str, next_attempt_at: datetime) -> None: ...
```

The port is deliberately minimal. Atomicity and concurrency safety are the **implementation's** documented responsibility, mirroring `ProcessedMessageStore` (*"Implementations should provide atomic check-and-set semantics to prevent races"*). `SELECT ... FOR UPDATE SKIP LOCKED`, claim timestamps, and abandoned-row reclamation live in the adapter, not the contract.

**3. `OutboundEventRegistry` — write-side translation.** A synchronous registration surface mirroring `register_translation`:

```python
def register_translation[T: IntegrationEvent](
    self,
    domain_type: type[DomainEvent],
    integration_class: type[T],
    topic: str,
    translator: Callable[[DomainEvent], T | None],
) -> None: ...
```

Registration is **synchronous and performs no I/O**. Re-registration **overwrites**, enabling hot-swap of integration event versions without restart — the same property ADR-060 established for the inbound registry. A translator returning `None` means the domain event stays internal.

**4. Translation happens inside `_write_outbox()`, within the transaction.** The UoW's collected `DomainEvent`s are mapped to `OutboxEntry` rows and appended atomically with the aggregate state. Handlers do not construct integration events, and no dual write occurs.

**5. `OutboundEventGateway` — a dumb relay.** A polling pump, symmetric with `InboundEventGateway` and following `SubscriptionRunner`'s loop discipline (ADR-048, ADR-049):

| Failure mode | Behaviour | Recovery |
|---|---|---|
| Broker unavailable / confirm timeout | `mark_failed` with increasing backoff | Retry |
| Broker nack | `mark_failed` with increasing backoff | Retry |
| Unknown topic (no registration) | Log error, `mark_failed`; **never** marked published | Retry at capped backoff |
| Payload fails `model_validate` | Log error, `mark_failed`; **never** marked published | Retry at capped backoff |

Every failure mode is **requeued with capped exponential backoff**, never dead-lettered. That is deliberate: registering a missing translation in a later deploy makes previously undeliverable rows publishable again, whereas dead-lettering would destroy them permanently. A failed entry never blocks the rest of the batch, and the backoff cap bounds the retry rate so a poison row cannot spin.

> **Amended by [ADR-065](ADR-065-outbox-retry-policy-and-dead-letter.md).** The retry is now bounded by a `max_attempts` budget, after which the entry is dead-lettered. Requeue and backoff behaviour is unchanged; `max_attempts=None` restores the retry-forever policy described here.

The gateway resolves the integration class from the outbox row's `topic` and calls `model_validate(payload)` — the flat payload pattern of ADR-022 and ADR-060. It never imports a domain type.

**6. The gateway is not an `EventBus` handler.** `EventBus._execute` logs and swallows handler exceptions (ADR-046), which is correct for in-process reactions and wrong for a side effect that cannot be replayed. The gateway runs as its own pump.

**7. Lifecycle.** `bootstrap()` starts the gateway after `broker.start()`; `Application.shutdown()` drains the gateway before `broker.stop()`, so no claimed-but-unpublished row is lost on a rolling restart.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Outbox stores `DomainEvent`s; the gateway translates at read time | Contradicts ADR-060, which rejects `EventRegistry`-based type resolution for gateways. Translation at read time also means already-written rows are re-interpreted by later code — replay instability for an at-least-once queue. |
| Domain-event handler calls `broker.publish()` directly | Post-commit and outside the transaction, so a crash between commit and publish loses the message permanently. Worse, `EventBus` swallows handler exceptions, so a broker outage is logged and the message silently dropped. |
| Publish to the broker inside `commit()`, no outbox | Makes the commit's success depend on broker availability. A broker outage becomes a write outage. |
| `OutboxUnitOfWork` ABC providing `_write_outbox()` | ADR-001 rejects forced extra inheritance: *"Forcing it to also inherit from a library ABC creates coupling and MRO complications."* The user already inherits `AbstractUnitOfWork`; a second library base compounds the hazard. The registry plus an `OutboxWriter` composed into the user's own `_write_outbox()` achieves the same result (see ADR-064). |
| Claim methods (`claim_due`, `release_claim`) on the port | Inflates a persistence port beyond the 2–3 method norm of `CheckpointStore`, `SnapshotStore`, and `EventStore`, and leaks a database locking strategy into the contract. |
| Reuse `SubscriptionRunner` as the relay base | `SubscriptionRunner` is an ABC sourced from `EventStore` + `Subscription` with a `process_batch` extension point; its source and error semantics differ. Reusing it would require changing a published, ADR-covered class. |

## Consequences

### Positive

- Committed state and its outbound events are persisted atomically — no dual-write window.
- Broker outages become backpressure instead of data loss; rows drain when the broker returns.
- The outbox is a durable audit record of everything published, satisfying auditability requirements.
- The relay is domain-ignorant: it knows topics and `IntegrationEvent` classes only.
- Outbox rows are the wire format, so delivery is replay-stable across deploys.
- Serialization needs no conversion layer — `IntegrationEvent` is primitives-only by validator, so `model_dump()` is already JSON-safe.

### Negative

- One more storage table and one more background component to operate.
- Translation runs inside the database transaction, so translators **must be pure** — no I/O, no enrichment calls.
- Delivery is at-least-once, so consumers must deduplicate on `message_id` (`ProcessedMessageStore.check_and_set` is the intended mechanism).
- There is no dead-letter state and no max-attempts policy. A row that can never be delivered is retried at the capped backoff interval until an operator intervenes; it is visible in `mark_failed` records and error logs but never leaves the fetchable set. A dead-letter store is a candidate follow-up. *Resolved by [ADR-065](ADR-065-outbox-retry-policy-and-dead-letter.md).*
- Removing a translation registration while unpublished rows exist strands those rows until the registration returns — deploy ordering matters.
- The gateway adds a hop between commit and broker.

### Neutral

- Translation may be registered for a subset of domain events; unregistered events never leave the boundary.
- Re-registration overwriting is intentional and matches ADR-060 — it enables version hot-swap.
- The outbox is optional: a system that does not configure an `OutboxStore` behaves exactly as before.

## References

- `src/pydomain/cqrs/outbox.py` — `OutboxEntry`, `OutboxStore`, `OutboundEventRegistry`, `OutboxWriter`
- `src/pydomain/infrastructure/message_broker.py` — `MessageBroker`, `OutboundEventGateway`
- `src/pydomain/cqrs/unit_of_work.py` — `_write_outbox()` extension point
- ADR-005: Publish-After-Commit
- ADR-022: Integration Events — Primitive-Only Payloads
- ADR-046: Event Handlers Fail Independently
- ADR-048: `SubscriptionRunner` — At-Least-Once
- ADR-051: `MessageBroker` Protocol — Separate Boundary from MessageBus
- ADR-059: `MessageSubscriber` Protocol
- ADR-060: `InboundEventGateway` — Bridging External Brokers to the Internal MessageBus
- ADR-064: `OutboxWriter` — Library-Owned Write-Side Join by Composition
- ADR-065: Outbox Retry Policy and Dead-Letter Queue
- `design-docs/01-outbound-event-gateway.{research,alignment,frame}.md`
