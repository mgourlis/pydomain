# ADR-065: Outbox Retry Policy and Dead-Letter Queue

## Status

Accepted

## Date

2026-09-29

## Context

ADR-063 established the outbound path (transactional outbox + `OutboundEventGateway`) and fixed its retry policy in decision 5:

> Every failure mode is **requeued with capped exponential backoff**, never dead-lettered. That is deliberate: registering a missing translation in a later deploy makes previously undeliverable rows publishable again, whereas dead-lettering would destroy them permanently.

The same ADR recorded the cost honestly under its own Negative section:

> There is no dead-letter state and no max-attempts policy. A row that can never be delivered is retried at the capped backoff interval until an operator intervenes; it is visible in `mark_failed` records and error logs but never leaves the fetchable set. A dead-letter store is a candidate follow-up.

This ADR is that follow-up. Three forces make the recorded trade-off no longer acceptable:

1. **Unbounded retry has no terminal state.** Under ADR-063 an entry is rescheduled at `max_delay` forever. Nothing in the system ever concludes "this message will not be delivered", so there is no event an operator can alert on and no set of rows an operator can triage. A message the system has silently given up on and a message that will succeed on the next pass are indistinguishable.

2. **A permanently-failing row is a permanent tax on every pass.** `fetch_unpublished(limit)` returns the oldest unpublished rows. A row that can never validate is fetched on every pass, fails, and is rescheduled — and with N such rows, each pass spends N of its `batch_size` slots republishing the same failures while newer, deliverable rows wait behind them. The gateway's independence guarantee (one failure does not block the batch) bounds the damage per pass but does not stop starvation across passes.

3. **The failure kind does not classify itself.** The distinction ADR-063's rationale relies on — "recoverable by a later deploy" versus "terminal" — is not derivable from the failure. An unknown topic is recoverable by deploy; a payload that violates its schema is usually terminal; a broker error is transient. But a broker may legitimately raise on a malformed payload, and a translation may be removed by mistake and restored. The exception type is a property of the adapter, not of the message's future deliverability.

## Decision

We will bound the retry budget and add an explicit dead-letter state.

**1. `max_attempts: int | None = 10` on `OutboundEventGateway`.** An entry that fails this many deliveries is dead-lettered instead of rescheduled. `None` disables the budget and preserves ADR-063's retry-forever behaviour for adopters who want it. The budget counts *failed attempts*, and `entry.attempts` is the count **before** the current attempt, so an entry with `max_attempts=10` is delivered at most ten times and dead-lettered on the tenth failure.

**2. `OutboxStore.mark_dead_lettered(message_id, *, error)` — a fifth port method.** `mark_failed` means "will retry"; the relay has no other way to express "will not". The entry must never be returned by `fetch_unpublished` again and must never be reported as published — it was not delivered.

**3. The budget is uniform; failures are not classified.** Every failure — unknown topic, validation error, broker exception — consumes one attempt from the same budget. The relay does not decide which failures are worth retrying, because it cannot know. A bounded budget reaches a terminal state for genuinely broken rows while still giving every recoverable failure the full budget to recover in.

**4. Exhaustion is logged at `ERROR`.** Dead-lettering is the one outcome where the library stops trying; it must never be silent. The log line carries the message id, topic, attempt count, and terminal error.

**5. Dead letters remain operator-visible and replayable.** The library removes them from the relay's working set but does not delete them. An operator who fixes the cause (deploys the missing translation, corrects the payload) can replay a dead letter by clearing its state. Whether dead letters live behind a status column in the outbox table or in a separate store is an **adapter decision**, consistent with `ProcessedMessageStore`.

**6. Claim mechanics stay out of the port.** Dead-lettering adds no locking, leasing, or reclamation contract; ADR-063's allocation of atomicity to the implementation is unchanged.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Failure-class taxonomy (`RetryableError` / `TerminalError`) raised by the broker | Requires the library to define an exception hierarchy that every broker adapter must implement correctly, and to trust it. A mis-classified terminal error destroys a recoverable message on the first failure — an unbounded loss traded for a bounded delay. It also forces a taxonomy onto adapters that already have their own exception types. |
| Per-failure-kind attempt budgets (e.g. many attempts for broker errors, one for validation errors) | Inherits the classification problem above, and multiplies configuration surface. Deferred: it can be expressed later by a pluggable policy without changing the port. |
| Keep unbounded retry (ADR-063's status quo) | Leaves a poison row competing for batch capacity forever and provides no terminal state for operators or alerting. |
| Express dead-lettering through `mark_failed` with a sentinel `next_attempt_at` (e.g. `datetime.max`) | Overloads one method with two meanings that the store cannot distinguish. The sentinel leaks into every adapter and any store that later reclaims stale claims would resurrect the row. |
| A separate `DeadLetterStore` port | Two ports for one feature. Adapters overwhelmingly want the dead letter to live beside its original row, and a separate store loses the atomic move between the two. |
| Gateway-local in-memory dead-letter list | Not durable, not operator-visible across restarts, and lost on a rolling deploy. |

## Consequences

### Positive

- The relay always makes progress: a dead letter stops occupying a batch slot, so deliverable rows are no longer starved by undeliverable ones.
- Giving up on a message is an explicit, `ERROR`-logged event that alerting can bind to, instead of an accumulation of `mark_failed` records no one reads.
- Dead letters are a triage list: a finite, inspectable set of messages the system could not deliver.
- `max_attempts=None` keeps ADR-063's semantics available, so the change is opt-out rather than forced.
- Replay after a fix remains possible, so the ADR-063 rationale — "a later deploy makes undeliverable rows publishable again" — still holds, within the budget.

### Negative

- **`OutboxStore` becomes the largest storage port in the library** at five methods — more than `EventStore` (3), `ProcessedMessageStore` (4), `CheckpointStore` (2), or `SnapshotStore` (2). Only `SagaRepository` (7) is larger, and it is a repository rather than a storage port. This is accepted because dead-lettering is a genuine second terminal outcome of the outbox's lifecycle, not a convenience method; the alternative is an adapter implementing two ports for one feature.
- **Dead-lettering converts silent retry into an operational obligation.** A dead letter nobody watches is strictly worse than a row that keeps retrying. The `ERROR` log is the mitigation, but it is a real cost: operators must now watch for it.
- **The uniform budget can terminate a row that a later deploy would have made deliverable** — precisely the outcome ADR-063 protected against. Mitigated but not eliminated: the budget is ten attempts rather than zero, and the entry is replayable. This is a deliberate move from "unbounded eventual delivery" to "bounded delivery with an explicit terminal state".
- Dead letters accumulate in storage; the library provides no retention policy or purge mechanism.

### Neutral

- `attempts` remains an adapter-maintained per-entry counter, unchanged from ADR-063 — only its meaning as a budget is new.
- `None` as "retry forever" matches the library's existing convention for optional budgets (`SagaPruningPolicy`, `SnapshotPolicy` treat `None` as "no bound").
- The dead-letter storage layout is unconstrained by the port.

## References

- `src/pydomain/cqrs/outbox.py` — `OutboxStore.mark_dead_lettered`, `OutboxEntry.attempts`
- `src/pydomain/infrastructure/message_broker.py` — `OutboundEventGateway.max_attempts`
- ADR-063: Outbound Event Gateway and Transactional Outbox
- ADR-064: `OutboxWriter` — Library-Owned Write-Side Join by Composition
- ADR-022: Integration Events — Primitive-Only Payloads
- ADR-051: `MessageBroker` Protocol — Separate Boundary from MessageBus
- `design-docs/01-outbound-event-gateway.frame.md`
