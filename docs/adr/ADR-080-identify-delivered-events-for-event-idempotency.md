# ADR-080: Identify Delivered Events for Event Idempotency

## Status

Accepted (2026-10-23). Amends [ADR-062](ADR-062-eventbus-application-layer-peer.md) (the key and the timing of
`EventIdempotencyBehavior`) and the statement of [ADR-058](ADR-058-messagebus-dispatch-domain-event.md) that
domain events pass through no pipeline behaviors. The commit that accepted it added notes to ADR-058, ADR-060,
ADR-061, ADR-062 (at §3 and l.114) and ADR-063, each saying "Takes effect with DCE-131 (W2-2)." (point 9,
B-162); their text is not edited. The implementation follows in the three phases of
[03-event-idempotency.frame.md](../../design-docs/03-event-idempotency.frame.md) (B-163), each a Feature Task
under DCE-131.

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
B-145 to B-168).

**1. An event is identified by its `event_id` (B-145, B-152).** `EventIdempotencyBehavior` keys on the
event's `event_id` on both paths, in-process and inbound. `causation_id` is tracing only. In-process events
already have one `event_id` each, and `stamp()` keeps it. For an inbound event, the `InboundEventGateway` sets
the translated domain event's `event_id` to `uuid5(INBOUND_EVENT_ID_NAMESPACE, integration_event.event_id)`,
unless the translator set `event_id` itself. Every redelivery of one integration event then gives the same
domain `event_id`, and the outbox `message_id` maps to it one to one. `uuid5` takes any string, so an
integration id that is not a UUID works too. The gateway does this before it stamps the tracing ids, whether or
not the payload has a `correlation_id`. The stamping rule itself is unchanged and belongs to W2-10 (B-148).

*How the gateway tells a translator's own id (B-156, B-158, B-166).* `model_fields_set` cannot:
`DomainEvent._ensure_event_id`, a `mode="before"` validator, puts a generated id into the input, so `event_id`
is always in it. The generation stays in that validator, which must stay `mode="before"`: a subclass's own
before-validator runs ahead of it and may set a deterministic `event_id`, which a wrap validator at the base
would not see (it runs first). The before-validator reports through a `ContextVar` whether it generated the id,
and a wrap validator on `DomainEvent` stores that in a private attribute, `_event_id_generated`, once the model
is built. The wrap validator only ever sets the marker to true, never writes false, and resets the `ContextVar`
token in `finally`: validating an existing instance (`Placed.model_validate(ev)`, or an instance put into a field
of another model) returns that same object, and writing false would turn a generated event explicit in place.
`model_copy`
and `stamp()` keep the attribute, and the gateway reads it: an event whose id was generated gets the derived id,
one whose translator, or a subclass validator, set `event_id` keeps it. `model_fields_set` and
`model_dump(exclude_unset=True)` are unchanged. `DomainEvent.__eq__` ignores the attribute, because Pydantic v2's
`__eq__` compares private attributes: a round trip through `model_dump()`, JSON, a subclass or a stamped copy
stays equal to the original (B-158). `__hash__` is not assigned, so Pydantic regenerates it for the new `__eq__`;
assigning `BaseModel.__hash__` broke the hashability tests, which pin it. A scratch model with a subclass before-validator printed
`generated / explicit: True False`, `subclass before-validator sets the id -> generated: False`,
`round trips equal (dump, json): True True`.

Three cases follow from the marker, and the docs name them:
- `DomainEvent.model_copy` clears the marker when `update` contains `event_id` (B-166), so an id set with
  `model_copy(update={"event_id": ...})` is explicit and the gateway keeps it, however the translator set it.
  `stamp()` does not update `event_id`, so it keeps the marker;
- `model_construct()` runs no validator, so its event reads as explicit: the gateway keeps its fresh id and
  nothing is deduplicated. Translators that use `model_construct()` are out of scope;
- the derived id ignores the topic (B-160). Integration event ids are unique per producer, so one id on two
  topics is a producer bug, and a consumer that handles both skips the second as a duplicate.

**2. A payload without `event_id` is not deduplicated (B-146, B-157 (g)).** The gateway checks the raw payload for an
`event_id` key. Without one it logs a WARNING that names the integration class and the topic only (ADR-071),
leaves the translator's `event_id` as it is, and dispatches. Each delivery therefore has a new key, and nothing
is skipped. The outbox always writes `event_id`, so only foreign producers send such payloads.

**3. The key names the consumer (B-147, B-148).** `EventIdempotencyBehavior(store, *, consumer: str | None =
None)`. The key is `uuid5(uuid5(EVENT_KEY_NAMESPACE, consumer), str(event.event_id))`, a `UUID`, so the
`ProcessedMessageStore` and `MessageReservationStore` Protocols keep their signatures. Keys cannot meet
command ids in practice: they are derived under pydomain's private namespace, so a collision needs a `uuid5`
(SHA-1) collision.
- When `consumer` is left out, the behavior uses the handler's `module:qualname`, read from `ctx.handler`
  (a callable instance's type for an instance). The constructor then emits a `DeprecationWarning`, once
  (B-157 (f)), that
  `consumer` will be required in 0.5.0 (fix plan W3-7).
- The fallback name collides across registrations: one function registered twice, two lambdas in one scope,
  every `functools.partial` (`functools:partial`), bound methods of two instances (`C.m`) and two callable
  instances of one class share a name. Renaming a handler changes its keys.
- One behavior instance serves one handler (B-159, B-167). The behavior learns its handler from `ctx.handler`,
  so it raises at the first dispatch to a second, different handler, not at registration. Handlers compare with
  `==`: a bound method by `__self__` and `__func__`; the same function registered twice is the same handler and
  is not detected. The error is `EventIdempotencyMisuseError`, a new `CQRSError` that names no content and is
  exported (W3-1 may place it). Until W2-3, `EventBus` logs it at ERROR and the second handler does not run. Its
  keys therefore never follow whichever handler ran. The collisions above are across instances, which this check
  cannot see; `consumer=` avoids them.
- The two namespaces are fixed constants, never changed, because stored keys outlive releases:
  `INBOUND_EVENT_ID_NAMESPACE = uuid5(NAMESPACE_URL, "https://github.com/mgourlis/pydomain/inbound-event-id")`
  (`52a1e11d-5cfe-52a6-971b-a1b2961dd04f`) and
  `EVENT_KEY_NAMESPACE = uuid5(NAMESPACE_URL, "https://github.com/mgourlis/pydomain/event-idempotency-key")`
  (`6122d529-5666-59f9-aeb0-cda93ddbb124`). They are private module constants, and their values are documented.

**4. The event is marked after its handler succeeds (B-149, B-150, B-157 (a) and (b), B-164, B-165).** When the store implements
`MessageReservationStore`, the behavior works like `IdempotencyBehavior` (ADR-079 point 4):
1. a `get(key)` hit skips the handler and logs at INFO, naming the event type and its `event_id` (today the
   line names `causation_id`: a log format change the docs mention);
2. `reserve(key)`. With no token it calls `get(key)` again: a hit skips, for a delivery that completed in
   between (B-142); otherwise it raises `EventInProgressError`, a new `CQRSError` that names the event type
   only;
3. it runs the handler. On return it calls `set(key, True)`, which completes the reservation. A `set` that
   raises is a type-only WARNING, as for commands (B-164, after B-129): the reservation stays until its TTL,
   and a later redelivery runs again (at-least-once);
4. a handler that raises gets its token released, and the error is re-raised unchanged.

`reserve`, `release` and `set` go through the same cancellation shield as for commands, so the store must work
from any task (B-123). Without `MessageReservationStore`, the behavior checks `contains(key)`, runs the handler,
then calls `check_and_set(key)`. Two concurrent deliveries can both run, which is documented as a known limit.
The fallback uses `contains` because the marks of `check_and_set` are not results (point 5), and `contains`
sees both. A `check_and_set` that raises after a successful handler is a type-only WARNING (B-165): the event
stays unmarked, the dispatch returns normally, and a redelivery runs the handler again. The handler's work and the mark are never atomic: a crash between them runs the handler again on
redelivery.

**5. Separate key spaces, no Protocol change (B-151, B-157 (c)).**
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
an in-process event the check costs, per handler, three store calls with a reservation store (`get`,
`reserve`, `set`) and one or two without (`contains`, then `check_and_set`), and skips nothing today. It is
ready for F-10.

**8. An outbox store refuses a duplicate `message_id` (B-155, B-157 (h)).** `OutboxStore.append` raises `CQRSError`, or a
subclass, for an entry whose `message_id` is already stored or repeated in the batch, and stores none of the
batch. This follows the Protocol's own words, "Raises `CQRSError` if persisting the entries fails", and a
database adapter gets it from a primary key on `message_id`. `append` runs in the commit
(`_write_outbox()`), so the command's commit fails and its scope rolls back. `FakeOutboxStore` does the same,
and `OutboxStoreContract` gains `test_append_refuses_a_duplicate_message_id`. Marks on unknown ids and the fetch
order stay contract gaps for a later cluster (fix plan §14).

**9. Notes on Accepted ADRs (B-157 (d), B-162).** The commit that accepted this ADR added them, each saying
"Takes effect with DCE-131 (W2-2).", as PR #124 did for ADR-079.
- ADR-062 §3 (l.67), where the key and the timing are decided: "**Amended by ADR-080 points 1, 3 and 4.**
  Takes effect with DCE-131 (W2-2)." followed by the new key and the mark after success.
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
| `model_fields_set` to detect a translator's id (B-145, B-156) | Always holds `event_id`, because the validator injects it |
| Stop injecting the id in the validator (B-156) | Drops `event_id` from `exclude_unset` dumps, a change for every event |
| The gateway always overrides `event_id` (B-156) | Undoes part of B-145: a translator's own id would be lost |
| A `register_translation(..., derive_event_id=True)` flag (B-158) | An API addition |
| The marker outside model state, as a private `UUID` subclass (B-158) | Subtle across `model_copy` and `stamp()` |
| Always key on the consumer plus the handler name; document one instance per registration (B-159) | Keys change when a handler is renamed even with `consumer=`; a document does not stop the mistake |
| Include the topic in the inbound derivation (B-160) | One id on two topics is a producer bug |
| Fold the CQRS-05 fix into upgrade notes 1 and 2 (B-161) | Hides a change users will see |
| Raise a failing fallback `check_and_set` to `EventBus` (B-165) | After W2-3 the gateway would NACK and redeliver an event whose handler already succeeded |
| Keep the `model_copy(update=event_id)` override and document "set `event_id` in the constructor" (B-166) | An explicit id would be replaced depending on how it was set |
| Cite B-129 by analogy only (B-164) | Leaves the event path's failure undecided |
| Raise `TypeError` for one instance on two handlers; leave the error to the fixer (B-167) | Not a `CQRSError`; an unrecorded public name |
| Write upgrade note 9 in the acceptance commit (B-168) | Upgrade notes ship with the code that changes; Phase 3 writes the others |
| Each implementation task adds its own ADR notes (B-162) | Accepted ADRs would describe a mix of old and new behaviour for weeks |
| Keep the implementation tasks in this ADR (B-163) | The template has no such section; W2-1 keeps them in its frame |
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

- Behaviour changes for 0.4.0, each with an upgrade note (B-154, B-157 (e), B-161):
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
  9. the ADR notes of point 9 are docs only;
  10. sibling events, two handlers sharing a store, and a store shared with `IdempotencyBehavior` now all run
      where they were skipped (the CQRS-05 fix); an event with `causation_id = None`, which used to pass
      unchecked, is now keyed on `event_id`, so dispatching one event object twice through
      `MessageBus.dispatch(event)` skips the second.
- Until W2-3, an `EventInProgressError` is logged at ERROR like any handler failure, and the gateway still ACKs.
- The `consumer` fallback keeps the old collisions for users who ignore the warning, until 0.5.0.
- Each idempotent handler costs one to three store calls per in-process event (point 7).

### Neutral

- The reservation semantics of ADR-079 are unchanged. A reservation left by a crash blocks its key until the
  TTL, as for commands.
- Deduplication stays at-least-once: the handler's work and its mark are not atomic (the event counterpart of
  W3-6).
- Constraints on other clusters: W2-3 receives the handler's own exception after the release, and NACKs an
  `EventInProgressError` (point 6). W2-10 owns stamping without `correlation_id`. W3-7 makes `consumer`
  required. W3-1 places `EventInProgressError` and `EventIdempotencyMisuseError` in the hierarchy.

## References

- `design-docs/03-event-idempotency.research.md`: the facts and scratch evidence
- `design-docs/03-event-idempotency.frame.md`: the implementation phases, tests and docs (B-163)
- [Decision log](../others/decision-log.md#12-library-behaviour) B-145 to B-168
- `docs/others/bug-fix-plan.md` §9.1 (W2-2), §9.2, §10 (W3-7), §14
- `src/pydomain/ddd/domain_event.py`: `_ensure_event_id`
- `src/pydomain/cqrs/behaviors.py`: `EventIdempotencyBehavior`
- `src/pydomain/cqrs/idempotency.py`: `ProcessedMessageStore`, `MessageReservationStore`
- `src/pydomain/infrastructure/message_subscriber.py`: `InboundEventGateway`
- `src/pydomain/testing/fake_processed_message_store.py`, `src/pydomain/testing/fake_outbox_store.py`
- [ADR-046](ADR-046-event-handlers-fail-independently.md), [ADR-058](ADR-058-messagebus-dispatch-domain-event.md),
  [ADR-060](ADR-060-inbound-event-gateway.md), [ADR-061](ADR-061-integration-event-tracing.md),
  [ADR-062](ADR-062-eventbus-application-layer-peer.md), [ADR-063](ADR-063-outbound-event-gateway-and-outbox.md),
  [ADR-071](ADR-071-library-failure-paths-carry-no-payload-values.md),
  [ADR-079](ADR-079-commit-inside-the-command-pipeline.md)
