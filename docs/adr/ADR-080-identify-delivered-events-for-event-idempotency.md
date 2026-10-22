# ADR-080: Identify Delivered Events for Event Idempotency

## Status

Proposed. Amends [ADR-062](ADR-062-eventbus-application-layer-peer.md) (the key and the timing of
`EventIdempotencyBehavior`) and the statement of [ADR-058](ADR-058-messagebus-dispatch-domain-event.md) that
domain events pass through no pipeline behaviors. When it is Accepted, notes are added to ADR-058 l.25 and
l.46-47, ADR-062 l.114, ADR-060 l.114, ADR-061 l.112 and ADR-063 l.113 (point 9); their text is not edited.
One open point: how the gateway tells a translator's own `event_id` (point 1).

## Date

2026-10-23

## Context

`EventIdempotencyBehavior` calls `ProcessedMessageStore.check_and_set(event.causation_id)` before the handler,
and does nothing after it (ADR-062). The 0.3.0 audit found the following (YouTrack DCE-147, fix plan cluster W2-2;
facts and scratch evidence in `design-docs/03-event-idempotency.research.md`):

1. **CQRS-05.** Every event that one command raises carries that command's `causation_id`, so they share one
   key. Only the first event reaches an idempotent handler. Two handlers that share a store share the key, so
   only the first one runs. With the fake, a store shared with `IdempotencyBehavior` skips every event, because
   the command's result is stored under the same id. ADR-062 promised that sibling events "are never
   incorrectly skipped" (l.114).
2. **CQRS-K2.** The mark is taken before the handler. A handler that fails stays marked, so its redelivery is
   skipped, and a concurrent redelivery is skipped while the first delivery is still running.
3. **INF-02.** The inbound gateway sets `causation_id` to the integration event's `event_id` only when the
   payload has a `correlation_id`; otherwise nothing is deduplicated. A payload without `event_id` gets a new
   one on each delivery. The translated domain event's own `event_id` is new on every redelivery unless the
   translator copies it.
4. **FAKE-06.** `FakeProcessedMessageStore` keeps results and `check_and_set` marks in one `dict`, while the
   how-to examples keep them apart and ask users to. The Protocol says nothing about key spaces.
5. **A2 and A31.** ADR-058 says domain events pass through no pipeline behaviors, but `register_event` takes
   behaviors. The `EventIdempotencyBehavior` docstring was corrected by DCE-126; its known-limit caveat
   remains.
6. **FAKE-05 (rest).** `FakeOutboxStore` accepts a second entry with a stored `message_id`, and the claim then
   hides it. The outbox `message_id` is the integration event's `event_id`, the identity a consumer
   deduplicates on.

Nothing in the library delivers an in-process event twice (`MessageBus` dispatches a command's events once,
after its commit). A second delivery comes from a broker through the `InboundEventGateway`, or from a future
local outbox-fed dispatcher (backlog F-10). `EventBus` swallows handler failures (ADR-046). The gateway
therefore always ACKs today: a redelivery after a failure needs W2-3 (DCE-132), or a subscriber that
redelivers on its own.

## Decision

The author decided each point on 2026-10-23 ([decision log](../others/decision-log.md#12-library-behaviour)
B-145 to B-155).

**1. An event is identified by its `event_id` (B-145, B-152).** `EventIdempotencyBehavior` keys on the
event's `event_id` on both paths, in-process and inbound. `causation_id` is tracing only. In-process events
already have one `event_id` each, and `stamp()` keeps it. For an inbound event, the `InboundEventGateway` sets
the translated domain event's `event_id` to `uuid5(INBOUND_EVENT_ID_NAMESPACE, integration_event.event_id)`,
unless the translator set `event_id` itself. Every redelivery of one integration event then gives the same
domain `event_id`, and the outbox `message_id` maps to it one to one. `uuid5` takes any string, so an
integration id that is not a UUID works too. The gateway does this before it stamps the tracing ids, whether or
not the payload has a `correlation_id`. The stamping rule itself is unchanged and belongs to W2-10 (B-148).

*Open point, how the gateway tells a translator's own id.* The author named `model_fields_set`. On this tree,
`DomainEvent._ensure_event_id`, a `mode="before"` validator, puts a generated id into the input, so `event_id`
is in `model_fields_set` whether or not the translator passed one. The design check printed
`constructed without event_id: True ['event_id', 'order_id']`; only `model_construct()` leaves it out.
Recommendation, awaiting the author: the validator records that it generated the id in a private attribute
(`_event_id_generated`), which `model_copy` and `stamp()` keep and the gateway reads. A scratch model shows it
works (`generated: True | explicit: False`, `copy keeps marker: True`), and `model_fields_set` and
`model_dump(exclude_unset=True)` stay as they are. The other options are to stop injecting the id, which drops
`event_id` from `exclude_unset` dumps, or to have the gateway always override the id.

**2. A payload without `event_id` is not deduplicated (B-146).** The gateway checks the raw payload for an
`event_id` key. Without one it logs a WARNING that names the integration class and the topic only (ADR-071),
leaves the translator's `event_id` as it is, and dispatches. Each delivery therefore has a new key, and nothing
is skipped. The outbox always writes `event_id`, so only foreign producers send such payloads.

**3. The key names the consumer (B-147, B-148).** `EventIdempotencyBehavior(store, *, consumer: str | None =
None)`. The key is `uuid5(uuid5(EVENT_KEY_NAMESPACE, consumer), str(event.event_id))`, a `UUID`, so the
`ProcessedMessageStore` and `MessageReservationStore` Protocols keep their signatures. Keys cannot collide with
command ids in practice: they are version-5 UUIDs, and command ids are version 4 or 7.
- When `consumer` is left out, the behavior uses the handler's `module:qualname`, read from `ctx.handler`
  (a callable instance's type for an instance). The constructor then emits a `DeprecationWarning` that
  `consumer` will be required in 0.5.0 (fix plan W3-7).
- The fallback keeps the collisions the research found: one function registered twice, or two lambdas in one
  scope, share a name. Renaming a handler changes its keys.
- The two namespaces are fixed constants, never changed, because stored keys outlive releases:
  `INBOUND_EVENT_ID_NAMESPACE = 52a1e11d-5cfe-52a6-971b-a1b2961dd04f` and
  `EVENT_KEY_NAMESPACE = 6122d529-5666-59f9-aeb0-cda93ddbb124` (each a `uuid5` of `NAMESPACE_URL` and a
  pydomain URL). They are private module constants, and their values are documented.

**4. The event is marked after its handler succeeds (B-149, B-150).** When the store implements
`MessageReservationStore`, the behavior works like `IdempotencyBehavior` (ADR-079 point 4):
1. a `get(key)` hit skips the handler and logs at INFO, as today, naming the event type and its `event_id`;
2. `reserve(key)`. With no token it calls `get(key)` again: a hit skips, for a delivery that completed in
   between (B-142); otherwise it raises `EventInProgressError`, a new `CQRSError` that names the event type
   only;
3. it runs the handler. On return it calls `set(key, True)`, which completes the reservation. A `set` that
   raises is a type-only WARNING: the reservation stays until its TTL, and a later redelivery runs again
   (at-least-once);
4. a handler that raises gets its token released, and the error is re-raised unchanged.

`reserve`, `release` and `set` go through the same cancellation shield as for commands, so the store must work
from any task (B-123). Without `MessageReservationStore`, the behavior checks `contains(key)`, runs the handler,
then calls `check_and_set(key)`. Two concurrent deliveries can both run, which is documented as a known limit.
The fallback uses `contains` because the marks of `check_and_set` are not results (point 5), and `contains`
sees both. The handler's work and the mark are never atomic: a crash between them runs the handler again on
redelivery.

**5. Separate key spaces, no Protocol change (B-151).**
- **Protocol docstring:** `ProcessedMessageStore` states that a `check_and_set` mark is not a result (`get`
  stays `MISSING`) and that `contains` covers both. It also names `EventIdempotencyBehavior`, not `EventBus`,
  as the caller of `check_and_set`.
- **Fake:** `FakeProcessedMessageStore` keeps its marks in a set of their own.
- **Contract:** `ProcessedMessageStoreContract` gains `test_check_and_set_creates_no_result`.
- **Fake test:** `test_check_and_set_after_set_returns_true` becomes `test_check_and_set_ignores_results`.
  `check_and_set` reads the marks only, as both how-to examples do. The contract does not pin this either way.

The reservation semantics of ADR-079 are unchanged.

**6. Failures, and what W2-2 leaves to W2-3 (B-153).** W2-2 ships before W2-3. The behavior releases the
reservation before the error leaves the pipeline, and re-raises it unchanged. A cancellation releases under the
shield. A dispatch closed while suspended (`GeneratorExit`) awaits nothing, so its reservation stays until the
TTL. A `release` that raises becomes a type-only note (`clean-up also failed: <Type>`). These constrain W2-3:
- whatever W2-3 makes `EventBus` and the gateway do with a failure, it receives the handler's own exception,
  after the release;
- an `EventInProgressError` counts as a failure, which the gateway NACKs, so the broker redelivers it later.

Until W2-3, `EventBus` logs an `EventInProgressError` like any handler failure, at ERROR with a traceback.

**7. In-process events follow the same rule (B-152).** The "use it for inbound events only" caveat goes. For
an in-process event the check costs one store round trip per handler and skips nothing today. It is ready for
F-10.

**8. An outbox store refuses a duplicate `message_id` (B-155).** `OutboxStore.append` raises `CQRSError`, or a
subclass, for an entry whose `message_id` is already stored or repeated in the batch, and stores none of the
batch. This follows the Protocol's own words, "Raises `CQRSError` if persisting the entries fails", and a
database adapter gets it from a primary key on `message_id`. `append` runs in the commit
(`_write_outbox()`), so the command's commit fails and its scope rolls back. `FakeOutboxStore` does the same,
and `OutboxStoreContract` gains `test_append_refuses_a_duplicate_message_id`. Marks on unknown ids and the fetch
order stay contract gaps for a later cluster (fix plan §14).

**9. Corrections to Accepted ADRs, added as notes when this ADR is Accepted.**
- ADR-058 l.25 and l.46-47: domain events pass through the pipeline behaviors registered with their handler;
  only the bus-level command pipeline and the unit of work are skipped (A2).
- ADR-062 l.114: sibling events are distinct keys only from this ADR on. The key is the event's `event_id` and
  the consumer, not `causation_id`.
- ADR-060 l.114 and ADR-061 l.112: `causation_id` is tracing only. Redelivery deduplication uses the derived
  `event_id` (point 1).
- ADR-063 l.113: consumers deduplicate on `message_id` through `EventIdempotencyBehavior`, which keys on the
  `event_id` derived from it, not through `check_and_set` on the id itself.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Per-path keys: `event_id` in-process, `causation_id` inbound (B-145) | The behavior cannot tell the paths apart |
| Docs only: translators must copy the integration id (B-145) | The trap stays |
| Discard a payload without `event_id` as poison (B-146) | Drops legitimate messages from foreign producers |
| A hash of the payload as its identity (B-146) | Two identical legitimate messages collide |
| The broker's message id (B-146) | Needs a new subscriber API |
| Derive the consumer name only; one store per handler (B-147) | Silent collisions; a footgun the docs cannot remove |
| Widen the key type to `str` or `Hashable` (B-148) | Breaks typed adapters |
| Fix the missing-`correlation_id` stamping here (B-148) | Tracing is W2-10's (A4); the key no longer depends on it |
| Only `check_and_set` after success (B-149) | No reservation: concurrent deliveries both run even with a store that could prevent it |
| Skip a concurrent duplicate silently (B-150) | The event is lost if the first delivery fails |
| Reuse `CommandInProgressError`; a shared `MessageInProgressError` (B-150) | Names a command for an event; W3-1 owns the hierarchy |
| No key-space change; a namespace parameter or a separate Protocol (B-151) | FAKE-06 stays; a Protocol change breaks adapters, and ADR-062 rejected a separate Protocol |
| Skip in-process events in code; inbound only in the docs (B-152) | Needs a marker the events do not carry; keeps a caveat |
| Ship W2-2 together with W2-3 (B-153) | Couples two decisions; the constraints of point 6 are enough |
| All three FAKE-05 gaps here; document only; another cluster (B-155) | Only the duplicate rule bears on event identity |

## Consequences

### Positive

- Every event and every consumer has its own key, so sibling events, several handlers and a store shared with
  `IdempotencyBehavior` no longer skip anything (CQRS-05).
- A failed handler is not marked, so a redelivery runs it again (CQRS-K2). With a reservation store, a
  concurrent duplicate is refused instead of being lost.
- One redelivered integration event always maps to one domain `event_id`, with or without `correlation_id`
  (INF-02).
- No Protocol signature changes.

### Negative

- Behaviour changes for 0.4.0, each with an upgrade note (B-154, plus point 8):
  1. marks taken before the upgrade, keyed by `causation_id`, are not recognised, so one redelivery in the
     upgrade window runs a handler again;
  2. a failed handler runs again on redelivery;
  3. inbound `event_id`s are derived;
  4. `consumer=` and its `DeprecationWarning`;
  5. a concurrent duplicate raises `EventInProgressError`;
  6. the fake's marks are no longer results, and `ProcessedMessageStoreContract` has a new case that
     third-party adapters must pass;
  7. a payload without `event_id` logs a WARNING and is not deduplicated;
  8. an outbox store refuses a duplicate `message_id`, and `OutboxStoreContract` has a new case;
  9. the ADR corrections of point 9 are docs only.
- Until W2-3, an `EventInProgressError` is logged at ERROR like any handler failure, and the gateway still ACKs.
- The `consumer` fallback keeps the old collisions for users who ignore the warning, until 0.5.0.
- Each idempotent handler costs a store round trip per in-process event.

### Neutral

- The reservation semantics of ADR-079 are unchanged. A reservation left by a crash blocks its key until the
  TTL, as for commands.
- Deduplication stays at-least-once: the handler's work and its mark are not atomic (the event counterpart of
  W3-6).
- Constraints on other clusters: W2-3 receives the handler's own exception after the release, and NACKs an
  `EventInProgressError` (point 6). W2-10 owns stamping without `correlation_id`. W3-7 makes `consumer`
  required. W3-1 places `EventInProgressError` in the hierarchy.

## Implementation tasks

Each task follows the test-first protocol (fix plan §4; CLAUDE.md steps ③ to ⑦). The tests named below are the
ones fix plan §9.2 asks for: sibling events and several handlers, failure then redelivery, and
ProcessedMessageStore contract cases. Each task removes the probe lines it fixes (probes README).

1. **Store key spaces and the outbox duplicate rule** (points 5 and 8).
   - Tests:
     - `ProcessedMessageStoreContract.test_check_and_set_creates_no_result`;
     - `test_check_and_set_ignores_results` (replaces `test_check_and_set_after_set_returns_true` in
       `tests/testing/test_fake_processed_message_store.py`);
     - `OutboxStoreContract.test_append_refuses_a_duplicate_message_id`;
     - `test_duplicate_in_one_batch_stores_nothing` (fake).
   - Docs: the `ProcessedMessageStore` and `OutboxStore` docstrings;
     `how-to/testing/use-fake-processed-message-store.md`; `how-to/cqrs/add-idempotency.md`;
     `concepts/infrastructure/outbound-event-gateway.md`; arc42 §5.3.6.
   - Upgrade notes: 6 and 8.
2. **Inbound event identity** (points 1 and 2; after the open point is answered).
   - Tests:
     - `test_redelivered_integration_event_gets_the_same_event_id`;
     - `test_inbound_event_id_is_uuid5_of_the_integration_event_id`;
     - `test_event_id_is_derived_without_correlation_id`;
     - `test_translator_event_id_is_kept`;
     - `test_payload_without_event_id_warns_and_is_not_deduplicated` (with a sentinel that the warning carries
       no payload value).
   - Docs: `concepts/infrastructure/inbound-event-gateway.md`;
     `how-to/infrastructure/configure-inbound-event-gateway.md`; `concepts/infrastructure/tracing.md`; arc42
     §8.2.
   - Upgrade notes: 3 and 7.
3. **`EventIdempotencyBehavior` keys, marks and refuses** (points 3, 4, 6 and 7; after task 1).
   - Tests:
     - the two `CQRS_05` strict xfails in `tests/cqrs/test_behaviors.py` lose their marker;
     - `test_two_handlers_sharing_a_store_both_run`;
     - `test_failed_handler_runs_again_on_redelivery`;
     - `test_duplicate_after_success_is_skipped`;
     - `test_concurrent_duplicate_raises_event_in_progress`;
     - `test_duplicate_that_completed_meanwhile_is_skipped`;
     - `test_store_without_reservations_marks_after_success`;
     - `test_key_is_uuid5_of_consumer_and_event_id`;
     - `test_missing_consumer_warns_and_keys_on_the_handler_name`;
     - `test_cancelled_handler_releases_its_reservation`;
     - `test_failed_release_is_a_type_only_note`;
     - `test_failed_set_is_a_type_only_warning`;
     - `test_inbound_redelivery_after_a_failure_runs_again` (gateway and a subscriber that redelivers; the NACK
       is W2-3's).
   - The three `TestEventIdempotencyBehavior` tests that key on `causation_id` are rewritten for `event_id`.
     The contract changed with this ADR; the tests are not weakened.
   - Docs: the behavior's docstring, which loses its known-limit caveat;
     `concepts/cqrs/pipeline-behaviors.md`; `concepts/cqrs/idempotency-and-locking.md`;
     `how-to/cqrs/handle-domain-events.md`; `how-to/cqrs/add-pipeline-behavior.md`;
     `concepts/infrastructure/message-bus.md`; arc42 §5.3.5; the glossary entry.
   - Upgrade notes: 1, 2, 4 and 5.

The notes of point 9 are added when this ADR is Accepted. The upgrade notes go to
`how-to/upgrade/upgrade-to-next-release.md`.

## References

- `design-docs/03-event-idempotency.research.md`: the facts and scratch evidence
- [Decision log](../others/decision-log.md#12-library-behaviour) B-145 to B-155
- `docs/others/bug-fix-plan.md` §9.1 (W2-2), §9.2, §10 (W3-7), §14
- `src/pydomain/cqrs/behaviors.py`: `EventIdempotencyBehavior`
- `src/pydomain/cqrs/idempotency.py`: `ProcessedMessageStore`, `MessageReservationStore`
- `src/pydomain/infrastructure/message_subscriber.py`: `InboundEventGateway`
- `src/pydomain/testing/fake_processed_message_store.py`, `src/pydomain/testing/fake_outbox_store.py`
- [ADR-046](ADR-046-event-handlers-fail-independently.md), [ADR-058](ADR-058-messagebus-dispatch-domain-event.md),
  [ADR-060](ADR-060-inbound-event-gateway.md), [ADR-061](ADR-061-integration-event-tracing.md),
  [ADR-062](ADR-062-eventbus-application-layer-peer.md), [ADR-063](ADR-063-outbound-event-gateway-and-outbox.md),
  [ADR-071](ADR-071-library-failure-paths-carry-no-payload-values.md),
  [ADR-079](ADR-079-commit-inside-the-command-pipeline.md)
