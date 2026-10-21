# ADR-074: The Outbox Relay and Registry Honour Their Contracts

## Status

Proposed. Amends [ADR-063](ADR-063-outbound-event-gateway-and-outbox.md),
[ADR-065](ADR-065-outbox-retry-policy-and-dead-letter.md),
[ADR-061](ADR-061-integration-event-tracing.md) and
[ADR-022](ADR-022-integration-events-primitive-payloads.md).

## Date

2026-10-21

## Context

ADR-063 introduced the transactional outbox and the `OutboundEventGateway` relay, ADR-065 bounded its retries
with a dead-letter state, and ADR-061 put `correlation_id` and `causation_id` on integration events. The
0.3.0 correctness audit (`docs/others/bug-audit-findings.md`; `docs/others/bug-audit-plan.md` §12, rows A3 and
A4) found that the relay, the registry and the shipped fake break promises those ADRs and their docstrings
make:

1. **One failing mark republishes the batch (INF-09).** `run_once()` called `mark_published` once, after the
   whole batch. When `mark_failed` or `mark_dead_lettered` raised for a later entry, or the pass was
   cancelled, every entry the broker had already accepted in that batch stayed unpublished and was delivered
   again on the next pass. Delivery is at-least-once (ADR-063), but this duplicate was caused by an unrelated
   entry, not by a crash between a publish and its own mark.
2. **The relay's arguments are not checked (INF-12, INF-18).** `max_attempts <= 0` dead-lettered an entry on
   its first failure; `batch_size <= 0` published everything with the fake and nothing with SQL `LIMIT 0`.
3. **`stop()` right after `start()` leaves the relay running (INF-11).** `start()` schedules `run()`, but
   `stop()` returned at once while the task had not yet set `_running`; `run()` then reset the stop request
   and polled forever. Two more lifecycle gaps sit in the same row: a broker `publish()` that never returns
   blocks `stop()`, and a task that died leaves `start()` a no-op.
4. **Delivered events lose their tracing (INF-13, CQRS-14, A4).** The entry's `correlation_id` and
   `causation_id` were never applied at delivery, so a published event carried tracing only if the
   translator stamped it, and broker headers were empty. The entry's `causation_id` was the domain event's own
   `causation_id` (the command id), while ADR-061 makes the domain event (`event_id`) the integration event's
   cause.
5. **Topic collisions and wrong translator classes create poison rows (CQRS-16, CQRS-25).**
   `register_translation` kept one integration class per topic, so a second domain type registered on the
   same topic with another class silently replaced the first, and stored rows of the first class were then
   rehydrated as the second. The translator's return value was never checked against the registered class, so
   a wrong class was stored and later failed validation, or lost fields, at delivery.
6. **Nested payload values are not checked (CQRS-15).** The primitive-only validator (ADR-022) checked only
   the top-level fields, so `{"at": datetime(...)}` in a `dict` field passed and the row failed later at JSON
   serialization. `stamp()` skipped the validator altogether.
7. **`FakeOutboxStore` ignores `limit <= 0` and has no claim (FAKE-05).** The `OutboxStore` docstring says
   `fetch_unpublished` must not hand one entry to two concurrent relays, but the fake returned the same rows
   to every fetch, and `limit=0` returned every row.
8. **ADR-063 types `message_ids` as `Sequence[UUID]` (A3)**, while the Protocol and every caller use `str`.

## Decision

We will make the relay, the registry and the fake keep the promises of ADR-061, ADR-063 and ADR-065 and of
their docstrings. Error, log and `repr()` text never carries payload values (ADR-071). The author decided
each open point on 2026-10-21 ([decision log](../others/decision-log.md#12-library-behaviour) B-57 to B-65,
and B-70 to B-73 after the PR #113 reviews).

1. **Each entry is marked when its own delivery finishes** (amends ADR-063 point 5). `run_once()` calls
   `mark_published([message_id])` right after the broker accepts that entry, before it delivers the next one.
   A store failure or a cancellation later in the batch can therefore duplicate at most the entry in flight,
   the at-least-once window ADR-063 accepts. A store error, from a mark or the fetch, ends the pass and
   propagates to `run()`, which logs it and backs off; the entries of the batch not yet delivered stay
   claimed until their claim goes stale (decision 2). The promise that entries are independent covers
   delivery failures only (B-64). Validating a stored payload counts as delivery whatever it raises: the relay
   catches any `Exception` from `model_validate`, describes it with `describe()` and spends an attempt, so a
   payload that cannot be validated is retried and then dead-lettered instead of ending every pass (B-70).
2. **A fetch claims what it returns** (amends ADR-063 point 2 and ADR-065 point 6, B-57, B-58). The
   `OutboxStore` Protocol now states that a later fetch does not return a claimed entry until it is marked or
   its claim goes stale, that `mark_failed` releases the claim (the entry then waits for `next_attempt_at`),
   and that `fetch_unpublished` raises `ValueError` for a `limit` below `1`. The port gains no method: how long
   a claim lasts and how it is stored stay adapter details. `OutboxStoreContract` gains
   `test_second_fetch_before_any_mark_returns_other_entries`, `test_failed_entry_is_released_from_its_claim`
   and `test_fetch_with_non_positive_limit_raises_value_error`. `FakeOutboxStore` takes a `claim_timeout`
   (default `timedelta(minutes=5)`, measured on its clock; `0` or less raises `ValueError`); the stale-claim
   behaviour is pinned by fake tests. `limit` must be an `int`, not a `bool`.

   **A published entry wins** (B-71). A claim can go stale while its relay is still publishing, so a second
   relay can deliver the same entry again: delivery stays at-least-once, and a publish that lasts longer than
   the claim timeout can be delivered twice. When the first relay's publish then fails, its `mark_failed` or
   `mark_dead_lettered` must not undo the publication: the Protocol states that both are no-ops for an entry
   already marked published, `FakeOutboxStore` implements it, and `OutboxStoreContract` gains
   `test_marks_after_publication_leave_the_entry_published`.
3. **The relay refuses arguments it cannot honour** (amends ADR-065 point 1, B-59). The constructor raises
   `ValueError`, naming the argument, for a `batch_size` or `max_attempts` that is not an `int` (a `bool`
   included) or is below `1` (`max_attempts=None` still means "retry forever"), a negative `base_delay` or
   `max_delay`, a `poll_interval_seconds` or `failure_backoff_seconds` that is negative or not finite, and a
   `max_delay` shorter than `base_delay`.
4. **`stop()` takes effect even before the background task has started** (INF-11, B-72). Each run of the
   relay loop has its own stop request. `start()` creates it with the task, so a `stop()` before the task
   begins makes it exit without a pass. `stop()` detaches the run before it waits for it, so a `start()`
   made while a `stop()` is still draining starts a fresh relay instead of doing nothing. `stop()` forgets a
   task that has already ended instead of awaiting it.
5. **A topic resolves to one integration class** (amends ADR-063 point 3, B-60). `register_translation`
   raises `ValueError` when another domain type already bound the topic to a different integration class;
   the message names the classes and the topic, never values, and nothing is registered. Several domain types
   may share a topic when they translate to the same class. Re-registering the same domain type still
   overwrites, so ADR-063's version hot-swap keeps working: rows already stored on the topic are then
   rehydrated as the new class, which must accept them. A topic stays bound when the domain type that bound
   it moves to another topic, on purpose: rows it already stored there still resolve to the original class
   (B-73). The error says so and names the first domain type that bound the topic, e.g.
   `Topic 't1' still resolves to X, first bound by A; cannot bind Y for B`.
6. **A translator returns exactly its registered class** (B-61). `to_entry()`, and so `OutboxWriter.write()`
   and the commit, raises `TypeError` when `type(result) is not integration_class`; a subclass is refused too,
   because rehydrating it as the registered class would drop its extra fields. The message names only the
   classes.
7. **Payloads are strict JSON at any depth** (B-62). The primitive-only check runs through dict values and
   list items; tuples and sets are refused as at the top level, and so are dict keys that are not `str`, NaN
   and infinity. The walk is iterative, with a cycle guard (the `id()` of the containers on the current
   path) and a maximum of 100 levels of dicts and lists, so no payload can raise `RecursionError`: a payload
   that is "nested too deep at <path>" or "contains a cycle at <path>" raises `ValidationError` like any
   other refusal (B-70). The message carries the field path, never the value:
   `Field 'data' has disallowed type 'datetime' at data['at']`. The path includes dict keys, which ADR-071
   does not protect: a key is a location, not a value. `IntegrationEvent.stamp()` validates its two
   ids against their fields and raises Pydantic's `ValidationError`, as `DomainEvent.stamp()` does (ADR-070,
   B-12).
8. **Delivery applies the entry's tracing, and the entry's cause is the domain event** (amends ADR-061,
   B-63). `OutboundEventRegistry.to_entry()` stores the domain event's `correlation_id` and, as
   `causation_id`, its `event_id`; for an unstamped domain event (no `correlation_id`, CQRS-26) both stay
   `None`. When an entry has both ids, the relay stamps them onto the rehydrated event, overriding any ids the
   translator set, and passes them to the broker as
   `headers={"correlation_id": "<str uuid>", "causation_id": "<str uuid>"}`, the event's own field names. For
   an entry without them the relay does not stamp the event and passes no headers, as ADR-061's inbound path
   skips stamping without a correlation id; ids the translator set in the payload stay.
9. **Errata to ADR-063 (A3):** `mark_published` takes `message_ids: Sequence[str]` and `mark_failed` takes
   `message_id: str`, as the Protocol always did.

**Known gaps, deferred to Wave 2** (B-65, B-72; backlog F-7, "stop(timeout=…) and restartable relay"):

- a broker `publish()` that never returns blocks `stop()`, which waits for the in-flight pass;
- after the background task has died, `start()` does nothing until `stop()` has been called;
- a `stop()` made before a task you created yourself for `run()` has begun is lost, and that task then runs
  until the next `stop()`; `start()` does not have this gap.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Keep one `mark_published` per batch and catch store errors around `mark_failed` | Still republishes the batch on a cancellation or a `mark_published` failure, and hides store outages |
| Catch a store error per entry and continue the pass (B-64) | Hides a failing store behind a loop that keeps publishing while its marks fail, multiplying duplicates |
| A single-relay fake with no claim (B-57) | Leaves the Protocol's "never to two relays" promise untested, and lets tests pass that a real adapter fails |
| Claim and release methods on the `OutboxStore` port (B-57) | Rejected by ADR-063 and ADR-065 point 6: pushes a locking strategy into every adapter |
| A 30-second default `claim_timeout` (B-57) | Short enough for a slow broker publish to outlast the claim in an ordinary test, so tests would see duplicate deliveries they did not set up |
| `claim_timeout=None`, claims that never go stale (B-57) | Deterministic, but it does not model crash recovery, so a test of a crashed relay would never see its rows again |
| `limit <= 0` returns `[]` (B-58) | Hides a caller bug, and SQL adapters disagree (`LIMIT 0` against a negative `LIMIT`) |
| Check only `batch_size` and `max_attempts`; allow `max_delay < base_delay` (B-59) | Negative sleeps and an inverted backoff cap are just as meaningless and fail later, less clearly |
| Raise on any change of class for a topic (B-60) | Breaks ADR-063's hot-swap of an integration event version by re-registering the same domain type |
| An `isinstance` check of the translator's result (B-61) | Accepts a subclass whose extra fields are lost when the row is rehydrated as the registered class |
| Keep non-`str` keys and non-finite floats (B-62) | Integer keys turn into strings in JSON and NaN is not JSON, so the row changes or fails at the broker |
| Leave tracing to the translator (`stamp_from`), or let the payload's ids win (B-63) | Every translator must remember it, and the entry already holds the ids ADR-061 promises end to end |
| Tracing ids only on the event, no headers; prefixed header names (`x-…`, `pydomain-…`) (B-63) | Brokers route and log on headers without parsing the body; the event's field names avoid a second vocabulary |
| Document a mark after publication only (B-71) | Leaves adapters free to dead-letter a delivered message, which an operator would then replay as a second delivery |
| A depth limit of 1000; a cycle guard with no depth limit (B-70) | 1000 levels is beyond what any broker payload needs and still near the recursion limits of consumers; without a limit, a deep but acyclic payload is still accepted and fails later |
| Document both stop/start races as gaps (B-72) | A `start()` during a `stop()` is what a supervisor restarting the relay does; leaving it a silent no-op loses the relay |
| Free a topic when its domain type moves to another topic (B-73) | Rows already stored on the old topic would then be rehydrated as whatever class binds it next |
| `stop(timeout=…)` now (B-65) | A new API with its own cancellation semantics; it belongs with the lifecycle work of Wave 2 |

## Consequences

### Positive

- A failure on one entry can no longer duplicate the rest of its batch.
- Two relays on one outbox no longer deliver the same row, and a crashed relay's rows come back.
- Every delivered event carries the tracing of the domain event that caused it, in the body and in headers.
- Poison rows from a topic collision or a wrong translator class are refused where they are caused, inside
  the commit or at registration, instead of burning the retry budget at delivery.
- A payload that is not strict JSON is refused at construction, with its path.

### Negative

- One store call per published entry instead of one per batch.
- Broker adapters whose `publish()` does not accept `**kwargs` fail every stamped delivery; the docs'
  `TracingPublisherMiddleware` and `RabbitMQBroker` examples did, and now accept and forward `headers`.
- Tests that inspected `FakeOutboxStore` through `fetch_unpublished` now claim the rows they read.
- A claim that goes stale while its relay is still publishing lets a second relay deliver the row again; the
  claim timeout must exceed the longest pass.
- Same-domain-type re-registration on a topic still rehydrates the old rows as the new class.

### Neutral

- Stored rows written before this change keep the command id as `causation_id`, and the relay applies it.
- The `OutboxStore` port keeps its five methods; only its documented promises grow.

## References

- `src/pydomain/infrastructure/message_broker.py` — `OutboundEventGateway`
- `src/pydomain/cqrs/outbox.py` — `OutboxStore`, `OutboundEventRegistry`
- `src/pydomain/cqrs/integration_events.py` — `IntegrationEvent`
- `src/pydomain/testing/fake_outbox_store.py` — `FakeOutboxStore`
- `src/pydomain/testing/contracts/outbox_store.py` — `OutboxStoreContract`
- `tests/infrastructure/test_outbox_relay_stateful.py` — the stateful outbox and relay test
- ADR-022: Integration Events — Primitive-Only Payloads
- ADR-061: Integration Event Distributed Tracing
- ADR-063: Outbound Event Gateway and Transactional Outbox
- ADR-065: Outbox Retry Policy and Dead-Letter Queue
- ADR-067: Protocol Contract Suites
- ADR-070: Aggregate Copies, Identity Types, Event Stamping and Composite Specifications
- ADR-071: Library-Owned Failure Paths Never Carry Payload Values
- ADR-072: Shipped Fakes Conform to Their Protocol Contracts
- arc42 §6.6 Outbound Delivery, §8.7 Integration Events, §9.25, §10.6
- `docs/others/bug-fix-plan.md` §8, W1-7
