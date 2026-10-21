# ADR-074: The Outbox Relay and Registry Honour Their Contracts

## Status

Proposed. Amends [ADR-063](ADR-063-outbound-event-gateway-and-outbox.md),
[ADR-065](ADR-065-outbox-retry-policy-and-dead-letter.md) and
[ADR-061](ADR-061-integration-event-tracing.md).

## Date

2026-10-21

## Context

ADR-063 introduced the transactional outbox and the `OutboundEventGateway` relay, ADR-065 bounded its retries
with a dead-letter state, and ADR-061 put `correlation_id` and `causation_id` on integration events. The
0.3.0 correctness audit (`docs/others/bug-audit-findings.md`; `docs/others/bug-audit-plan.md` §12, rows A3 and
A4) found that the relay, the registry and the shipped fake break promises those ADRs and their docstrings
make:

1. **One failing mark republishes the batch (INF-09).** `run_once()` calls `mark_published` once, after the
   whole batch. When `mark_failed` or `mark_dead_lettered` raises for a later entry, or the pass is cancelled,
   every entry the broker already accepted in that batch stays unpublished and is delivered again on the next
   pass. Delivery is at-least-once (ADR-063), but the duplicate here is not caused by a crash between a
   publish and its mark: it is caused by an unrelated entry.
2. **The relay's arguments are not checked (INF-12, INF-18).** `max_attempts <= 0` dead-letters an entry on
   its first failure; `batch_size <= 0` publishes everything with the fake and nothing with SQL `LIMIT 0`.
3. **`stop()` right after `start()` leaves the relay running (INF-11).** `start()` schedules `run()`, but
   `stop()` returns at once while the task has not yet set `_running`; `run()` then resets the stop request
   and polls forever.
4. **Delivered events lose their tracing (INF-13, CQRS-14, A4).** The entry's `correlation_id` and
   `causation_id` are never applied at delivery, so a published event carries tracing only if the translator
   stamped it. The entry's `causation_id` is also the domain event's own `causation_id` (the command id),
   while ADR-061 says the integration event's cause is the domain event (`event_id`).
5. **Topic collisions and wrong translator classes create poison rows (CQRS-16, CQRS-25).**
   `register_translation` keeps one integration class per topic, so a second domain type registered on the
   same topic with another class silently replaces the first, and stored rows of the first class are then
   rehydrated as the second. The translator's return value is never checked against the registered class,
   so a wrong class is stored and later fails validation, or loses fields, at delivery.
6. **Nested payload values are not checked (CQRS-15).** The primitive-only validator (ADR-022) checks only
   the top-level fields, so `{"at": datetime(...)}` in a `dict` field passes, and the row fails later at JSON
   serialization.
7. **`FakeOutboxStore` ignores `limit <= 0` and has no claim (FAKE-05).**
8. **ADR-063 types `message_ids` as `Sequence[UUID]` (A3)**, while the Protocol and every caller use `str`.

## Decision

We will make the relay, the registry and the fake keep the promises of ADR-061, ADR-063 and ADR-065 and of
their docstrings. Error, log and `repr()` text never carries payload values (ADR-071).

1. **Each entry is marked when its own delivery finishes** (amends ADR-063 point 5). `run_once()` calls
   `mark_published([message_id])` right after the broker accepts that entry, before it delivers the next one.
   A store or cancellation failure later in the batch can therefore duplicate at most the entry in flight,
   which is the at-least-once window ADR-063 accepts.
2. **Delivery applies the entry's tracing, and the entry's cause is the domain event** (amends ADR-061).
   `OutboundEventRegistry.to_entry()` stores `causation_id = str(event.event_id)`; the relay stamps the
   rehydrated event with the entry's `correlation_id` and `causation_id` before it publishes.
3. **`stop()` takes effect even when the background task has not started yet** (INF-11).
4. **Nested payload values are validated** (amends ADR-022's validator as described by ADR-063): a value
   inside a `dict` or `list` field must itself be a primitive.
5. **Errata to ADR-063:** `mark_published` takes `Sequence[str]` and `mark_failed` takes `message_id: str`,
   as the Protocol always did.

**Open points for the author** (not decided; this ADR stays Proposed until they are): the exception types and
bounds of the argument checks; what counts as a topic collision and how a wrong translator class is detected;
the edge cases of nested validation and of tracing at delivery; whether a store failure on one entry ends the
pass; and what a claim in `FakeOutboxStore` and the `OutboxStore` contract means (ADR-063 and ADR-065 point 6
keep claim mechanics out of the port).

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Keep one `mark_published` per batch and catch store errors around `mark_failed` | Still republishes the batch on a cancellation or on a `mark_published` failure, and hides store outages |
| Leave tracing to the translator (`stamp_from`) | Every translator must remember it; the entry already holds the ids, and ADR-061 promises end-to-end tracing |

## Consequences

### Positive

- A failure on one entry can no longer duplicate the rest of its batch.
- Every delivered event carries the tracing of the command that caused it.

### Negative

- One store call per published entry instead of one per batch.

### Neutral

- Stored rows written before this change keep the command id as `causation_id`.

## References

- `src/pydomain/infrastructure/message_broker.py` — `OutboundEventGateway`
- `src/pydomain/cqrs/outbox.py` — `OutboxStore`, `OutboundEventRegistry`
- `src/pydomain/cqrs/integration_events.py` — `IntegrationEvent`
- `src/pydomain/testing/fake_outbox_store.py` — `FakeOutboxStore`
- ADR-022: Integration Events — Primitive-Only Payloads
- ADR-061: Integration Event Distributed Tracing
- ADR-063: Outbound Event Gateway and Transactional Outbox
- ADR-065: Outbox Retry Policy and Dead-Letter Queue
- ADR-071: Library-Owned Failure Paths Never Carry Payload Values
- `docs/others/bug-fix-plan.md` §8, W1-7
