# Research: Event Idempotency and Message Identity

- **Date:** 2026-10-23
- **Issue:** DCE-147 (Feature DCE-131, fix plan cluster W2-2)
- **Scope:** How in-process and inbound domain events are identified; what `EventIdempotencyBehavior` keys on and
  when it marks; how one event with several handlers is deduplicated; the key spaces of `ProcessedMessageStore`
  in the Protocol, the fake, the how-to examples and the contract suites; how `MessageReservationStore` could
  apply; what the ADRs and docs promise.
- **Method:** Source reading at `origin/dev` (1167fde, W2-1 complete), the audit probes in
  `tests/support/probes/`, and two scratch scripts run against this tree (not committed; their output is quoted
  below). Facts only: no options or recommendations.
- **Paths:** source paths are relative to `src/pydomain/`; `l.N` is a line number.

The rows in scope are CQRS-K2, CQRS-05, INF-02, FAKE-06, A2, A31 and the rest of FAKE-05. Their audit entries:
`docs/others/bug-audit-findings.md` l.82, 86, 128, 227, 269; `docs/others/bug-audit-plan.md` l.349, 362, 553,
592, 593, 631, 668. Decision log P-31 (l.56) assigned FAKE-05 (rest) to this cluster.

---

## 1. How an event is identified

### Files

- `ddd/domain_event.py`: the fields (l.48-52), `_ensure_event_id` (l.56-61), `stamp` (l.63-86).
- `cqrs/command_bus.py`: tracing resolution (l.236-245), the context (l.247-254), the terminal step (l.100-103).
- `cqrs/unit_of_work.py`: `_collect_and_stamp` (l.580-595).
- `cqrs/integration_events.py`: `IntegrationEvent` fields (l.107-111), `stamp_from` (l.167-196).
- `cqrs/outbox.py`: `OutboxEntry` (l.42-88), `OutboundEventRegistry.to_entry` (l.371-416).
- `infrastructure/message_subscriber.py`: `InboundEventGateway._process_message` (l.209-300).

### Findings

**F1.1 Three ids on every domain event.** `event_id: UUID` defaults to the class's id generator (UUIDv7 by
default, l.46-48; a validated payload without one gets a fresh id, l.56-61). `correlation_id` and
`causation_id` are `UUID | None`, default `None` (l.51-52).

**F1.2 In-process events: one `causation_id` per command.** `CommandBus.dispatch` resolves
`causation_id = command.causation_id or command.command_id` and the same for `correlation_id` (l.236-237),
sets both on the UoW with `setattr` (l.244-245), and `_collect_and_stamp` stamps every event of the scope with
them (`unit_of_work.py` l.588-595). `stamp` is `model_copy(update=..., deep=True)` (l.86), so it keeps
`event_id`. The command's `command_id` is also in `ctx.metadata["command_id"]` (l.253), the key
`IdempotencyBehavior` uses.

**F1.3 In-process dispatch runs once per committed command.** `MessageBus.dispatch(Command)` awaits
`CommandBus.dispatch`, then `EventBus.dispatch_many(events)` (`message_bus.py` l.196-199). A duplicate command
answered by `IdempotencyBehavior` returns no events (`command_bus.py` l.191-195). Nothing in the library
delivers an in-process event a second time; the backlog item F-10 (a local outbox-fed dispatcher, decision log
l.241) would.

**F1.4 Outbox entries carry the integration event's id.** `to_entry` builds the integration event with the
registered translator and writes `message_id=integration_event.event_id` and, for a stamped event,
`causation_id=str(event.event_id)` (`outbox.py` l.408-416). `IntegrationEvent.event_id` is a `str` defaulting
to a fresh UUIDv7 string (`integration_events.py` l.107). The relay rehydrates the payload with
`model_validate` (`message_broker.py` l.382), which keeps it.

**F1.5 Inbound events: the translator makes a new domain event.** `_process_message` hydrates the payload,
calls the translator, then stamps the result with `correlation_id=UUID(ie.correlation_id)` and
`causation_id=UUID(ie.event_id)`, only when the integration event has a `correlation_id` (l.269-274). Ids
that do not parse are logged by field name and the event is dispatched unstamped (l.275-288). The domain
event's own `event_id` comes from the translator: a translator that builds the event without one gets a new
id per delivery.

**F1.6 Without `event_id` in the payload, every redelivery has a new integration id** (INF-02): the default
factory runs at validation. Without `correlation_id`, nothing is stamped and `causation_id` stays `None`.

### Scratch evidence

`identity.py` (S1 to S3):

```
== S1 in-process event identity ==
siblings: distinct event_id: True | causation_id == command_id: True | correlation_id == command_id: True
handler saw the returned objects' event_ids: True
stamp keeps event_id: True
same command run twice (no IdempotencyBehavior): same event_id: False | same causation_id: True
duplicate command (IdempotencyBehavior): events first/second = 1 0
== S2 outbox entry identity ==
message_id == payload event_id: True
entry.causation_id == domain event_id: True
translating one domain event twice gives the same message_id: False
relay rehydration keeps event_id: True
== S3 inbound event identity ==
redelivery with event_id+correlation_id: domain event_id same: False | causation_id same: True | causation_id == integration event_id: True
redelivery without correlation_id: causation_ids: [None, None]
redelivery without event_id: causation_id same: False
translator that copies event_id: domain event_id same: True
```

The audit probe `inf/inbound_probe.py` prints the same for INF-02:

```
INF-02 case A (event_id, no correlation_id): handler ran 2 times; causation_ids: [None, None]
INF-02 case B (correlation_id, no event_id): handler ran 2 times; distinct causation_ids: 2
```

Summary of what stays stable across a second delivery:

| Path | `event_id` of the domain event | `causation_id` of the domain event |
|---|---|---|
| In-process, sibling events of one command | distinct per event | shared: the command's |
| In-process, the same command run again without `IdempotencyBehavior` | new | the same (the command's) |
| Inbound, payload with `event_id` and `correlation_id` | new per delivery, unless the translator copies it | the integration `event_id`: stable |
| Inbound, payload without `correlation_id` | new per delivery | `None` |
| Inbound, payload without `event_id` | new per delivery | a new integration id per delivery |

---

## 2. What `EventIdempotencyBehavior` keys on, and when it marks

### Files

- `cqrs/behaviors.py`: `EventIdempotencyBehavior` (l.672-730).

### Findings

**F2.1 The key is `event.causation_id`.** `handle` passes through when the message is not a `DomainEvent` or
has no `causation_id` (l.717-718), then calls `self._store.check_and_set(event.causation_id)` (l.720). On
`True` it logs "Skipping duplicate event <type> (causation_id=<id>)" at INFO on `pydomain.pipeline` and
returns `None` without calling `next()` (l.721-728).

**F2.2 The mark is taken before the handler** (CQRS-K2). `check_and_set` marks the id at l.720; `next()` runs
at l.730. There is no code after `next()`: a handler that raises leaves the mark, and nothing releases it.

**F2.3 The key ignores the handler.** Neither the handler (`ctx.handler`, set by `MessagePipeline._run` before
the first behavior, l.138) nor the pipeline appears in the key.

**F2.4 The docstring states the in-process key and the known limit** (l.688-699): in-process events carry the
command's `causation_id`; "only the first event a command raises reaches an idempotent handler ... and none
does if the store is shared with `IdempotencyBehavior` (CQRS-05). To be fixed in 0.4.0 (W2-2)." DCE-126
(d2bccb8) wrote this text, replacing the claim that in-process events have no `causation_id` (A31, first half).

### Scratch evidence: failure, then redelivery; concurrent redelivery

`identity.py` S4: one event, a handler that waits on a gate and then raises; a second delivery while the first
waits, a third after it failed.

```
while the first delivery runs: store marks the key: True | get() returns: True
concurrent redelivery ran the handler: False
after the failure, redelivery ran the handler: False (calls=1)
MessageBus.dispatch(event) with a failing handler raised: no
```

The audit probes print the same (`cqrs/p_known.py`; `inf/inbound_probe.py`):

```
K2 handler calls after failure + redelivery = 1 (1 means redelivery skipped)
INF-01b failing handler with EventIdempotencyBehavior: calls over 2 deliveries = 1 outcomes ['ACK', 'ACK']
```

---

## 3. One event with several handlers

### Files

- `cqrs/event_bus.py`: `register` (l.45-72), `_execute` (l.106-125).

### Findings

**F3.1 One pipeline per registration.** `register` appends a new `MessagePipeline(handler, behaviors)` to the
list for the event type (l.70-72); the same function registered twice gives two pipelines. The behaviors list is
the caller's: one behavior instance can serve several registrations.

**F3.2 One context per handler, no shared state.** `_execute` builds a fresh `MessageContext` for each pipeline,
with `metadata={}` and the event's own tracing ids (l.109-117). Handlers run one after another, in registration
order.

**F3.3 Several handlers sharing a store share one key**, because the key is the event's `causation_id` alone
(F2.1, F2.3): the first handler's mark makes every later one skip.

**F3.4 What a behavior can tell about its handler.** `identity.py` S6 prints `(__module__, __qualname__,
type)` of `ctx.handler` for four registrations:

```
('__main__', 's6_handler_identity.<locals>.fn_handler', 'function')
('__main__', 's6_handler_identity.<locals>.fn_handler', 'function')
('__main__', None, 's6_handler_identity.<locals>.ClassHandler')
('__main__', 's6_handler_identity.<locals>.<lambda>', 'function')
```

A function registered twice gives the same name; a callable instance has no `__qualname__` (its type has);
every lambda in one scope is `<lambda>`. Nothing in `EventBus` or `MessagePipeline` names a registration.

### Scratch evidence: CQRS-05

The audit probe `cqrs/p_04_09.py`:

```
CQRS-05a two ItemAdded from one command, guarded handler saw=['SKU-A'] (expected 2)
CQRS-05b two handlers, shared store: ran=['projection'] (expected both)
CQRS-05c store shared with IdempotencyBehavior: event handler ran=[] (expected 1)
CQRS-05 docstring: in-process event causation_id=01a1657c-... == command_id: True; store4 keys=1
```

The strict xfails `test_a_store_shared_with_command_idempotency_keeps_the_events` and
`test_every_event_of_one_command_is_handled` (`tests/cqrs/test_behaviors.py` l.911-927, marker `CQRS_05`,
`raises=AssertionError`, reason "DCE-126 review: CQRS-05, event keys reuse the command id (W2-2)") pin 05c
and 05a.

---

## 4. `ProcessedMessageStore`: key spaces

### Protocol (`cqrs/idempotency.py` l.19-66)

- Four methods, every one keyed by `message_id: UUID`: `get`, `set`, `contains`, `check_and_set`. No delete,
  no TTL, no namespace parameter.
- The class docstring says the store is "used by `IdempotencyBehavior` (commands cache results) and
  `EventBus` (events check-and-set)" (l.23-25); the caller of `check_and_set` is `EventIdempotencyBehavior`,
  not `EventBus`.
- `contains` is "`True` if *message_id* has already been processed" (l.53-55); the Protocol does not say
  whether a `check_and_set` mark and a `set` result are one key space.

### The fake (`testing/fake_processed_message_store.py`)

- Results and marks share one `dict`, `_store` (l.28-29, l.45); `check_and_set` stores `True` there
  (l.63-72). Reservations have their own `dict` (l.46).

### The how-to examples (`docs/diataxis/how-to/cqrs/add-idempotency.md`)

- In-memory example: `_results` and `_seen` apart; `contains` checks both (l.50-68). The text asks to "keep the
  marks of `check_and_set()` apart from the cached results ... and give each behavior its own store (or key
  space)" (l.71).
- Redis example: `cmd:<id>` (results, TTL 86400 s), `seen:<id>` (marks, TTL 86400 s), `reserved:<id>`
  (reservations, TTL 300 s); `contains` checks `cmd:` and `seen:` (l.162-215, l.217).

### Contract suites and tests that touch the key spaces

| Where | Test | What it pins |
|---|---|---|
| `testing/contracts/processed_message_store.py` l.106-114 | `test_check_and_set_marks_id_processed` | after `check_and_set(id)`, `contains(id)` is true |
| `testing/contracts/message_reservation_store.py` l.165-176 | `test_reservations_do_not_collide_with_check_and_set` | reserving never marks, `check_and_set` never reserves |
| `tests/testing/test_fake_processed_message_store.py` l.154-161 | `test_check_and_set_marks_contains` | the same as the contract, on the fake |
| same file l.177-186 | `test_check_and_set_after_set_returns_true` | **`check_and_set` after `set` returns `True`: one key space for results and marks in the fake** |
| `tests/cqrs/test_behaviors.py` l.782-865 | `TestEventIdempotencyBehavior` (3 tests) | the key is `causation_id`; `None` passes through |

### Scratch evidence

`identity.py` S5: one store for `IdempotencyBehavior` and two `EventIdempotencyBehavior` handlers; one command
raising two `ItemAdded`.

```
fake: one store for both behaviors, two handlers, two events -> ran []
how-to example: one store for both behaviors, two handlers, two events -> ran ['a:SKU-A']
fake: after check_and_set, get() returns: True
```

With the how-to's separate spaces, CQRS-05c does not occur; CQRS-05a and 05b still do. With the fake, a mark
reads back from `get` as a cached result `True`.

FAKE-06 (`bug-audit-findings.md` l.269) is "Design: the protocol defines one key space; the defect is CQRS-05".

---

## 5. What a failed handler does to the delivery

**F5.1 `EventBus` swallows handler failures.** `_execute` catches `Exception` per pipeline and logs it with a
traceback (`event_bus.py` l.118-125, ADR-046). `MessageBus.dispatch(DomainEvent)` therefore returns normally
(`identity.py` S4: "raised: no").

**F5.2 The gateway's NACK path is unreachable through `MessageBus`.** `_process_message` re-raises a dispatch
failure "so the subscriber can NACK" (`message_subscriber.py` l.22-24, l.291-300), but the dispatch does not
raise (F5.1): `inf/inbound_probe.py` prints `INF-01 handler calls: 1 subscriber outcome: ['ACK']`. INF-01 is
W2-3's (fix plan §9.1).

**F5.3 A cancellation is not swallowed.** `_execute` catches `Exception` only (l.120), so a cancellation of a
handler propagates out of `dispatch`, past the remaining handlers.

---

## 6. `MessageReservationStore` (ADR-079) and events

**F6.1 Shape.** `reserve(message_id: UUID) -> Hashable | None` and `release(message_id, token)`
(`idempotency.py` l.69-127). `set(message_id, result)` completes a reservation; a completed reservation keeps
refusing `reserve` "at least until the TTL" (B-142); a reservation is not a result (`get` returns `MISSING`);
reservations have a key space of their own, apart from results and `check_and_set` marks (l.81-102, B-117).
The store must work from any task, because the behavior calls it through the shield (l.104-110, B-123).

**F6.2 How `IdempotencyBehavior` uses it** (`behaviors.py` l.576-625): `get` → `reserve` (no token: `get`
again, else `CommandInProgressError`) → `next()` → on failure before the commit `release(token)`; on success
`set(id, result)`. It detects support with `isinstance(store, MessageReservationStore)` (l.572-574).

**F6.3 The flow works on an event-derived key with the fake.** `identity.py` S7, with a key
`uuid5(<synthetic namespace>, "orders.projection:<event_id>")`:

```
derived key is a UUID: True | version: 5
reserve twice: True True
after release, reserve again: True
after set: get -> True | reserve -> None
fake accepts a tuple key at runtime: True
```

A `uuid5` key fits the Protocol's `UUID` type; a tuple key works on the fake at runtime only (the annotation is
`UUID`).

**F6.4 TTLs in the Redis example differ.** `reserved:` keys live 300 s, `cmd:` keys 86400 s (add-idempotency.md
l.166, l.188, l.207). After the reservation TTL, `reserve` succeeds again for a completed id; the behavior's
first `get` finds the result first.

**F6.5 ADR-079's constraint on this cluster** (Neutral, l.348-349): "W2-2 can reuse `MessageReservationStore`
for 'mark only after success', and keeps the key spaces of point 4 separate without changing the reservation
semantics."

---

## 7. What the ADRs and docs promise

| Source | Statement | Matches the code? |
|---|---|---|
| ADR-058 l.25, l.46-47 | Domain events "should *not* pass through a UoW or pipeline behaviors"; "No UoW, no pipeline behaviors" | No: `register_event` takes behaviors (`message_bus.py` l.129-156) and `EventIdempotencyBehavior` exists (A2) |
| ADR-060 l.69-70 and the ACK/NACK table (l.72-79) | Dispatch failures propagate, so the subscriber NACKs | No (F5.2; W2-3) |
| ADR-060 l.114; ADR-061 l.112 | `causation_id` = the integration event's `event_id`, "providing consumer-side idempotency for broker redelivery" | Yes, when the payload has `correlation_id` and both ids parse (F1.5, F1.6) |
| ADR-061 l.70 (snippet) | The gateway stamps when both `correlation_id` and `causation_id` are present | No: the code gates on `correlation_id` only (A4, W2-10) |
| ADR-062 l.91 | Per-handler behavior "lets individual handlers opt in" | Opt-in is per handler; the key is not (F2.3) |
| ADR-062 l.114 | "sibling events from the same command execution are never incorrectly skipped" | No (CQRS-05a) |
| ADR-062 l.102 | Rejects bus-level dedup partly because it "would need a `dispatch()`/`dispatch_many()` split ... to avoid false dedup of sibling events" | The behavior has the same problem (CQRS-05a) |
| ADR-046 | Handlers fail independently; exceptions caught, logged, swallowed | Yes (F5.1) |
| ADR-079 Neutral l.348-349 | W2-2 may reuse `MessageReservationStore`, keeps the key spaces separate | Not yet used for events |
| `concepts/cqrs/pipeline-behaviors.md` l.143-157 | Key is `causation_id`; known limit: inbound events only | Yes |
| `how-to/cqrs/add-pipeline-behavior.md` l.142 | The same known limit | Yes |
| `how-to/cqrs/add-idempotency.md` l.71 | Keep the marks apart from results; one store (or key space) per behavior | The fake does not (§4) |
| `concepts/infrastructure/message-bus.md` l.113 | Behaviors on the event handler are applied; the bus-level pipeline is skipped | Yes |
| arc42 §5.3.5 (`05-building-block-view.md` l.394) | "Keys `check_and_set()` on the event's `causation_id`; use it for inbound events" | Yes |
| arc42 §5.3.6 (l.400) | `ProcessedMessageStore` "tracks processed message IDs (commands and events)" | Yes; key spaces unstated |
| arc42 §9.16 (`09-design-decisions.md` l.580) | ADR-062's narrative | As ADR-062 |
| `concepts/cqrs/idempotency-and-locking.md` l.108; ADR-079; B-124 | In-process handlers are best-effort; the outbox path is the at-least-once one | Yes (F1.3) |

The pages the fix plan §9.2 lists for this cluster: `concepts/cqrs/idempotency-and-locking.md`,
`concepts/infrastructure/inbound-event-gateway.md` (which today says nothing about redelivery or dedup),
`how-to/cqrs/handle-domain-events.md` (nothing about dedup; l.62 says in-process dispatch is best-effort) and
`how-to/testing/use-fake-processed-message-store.md` (commands only).

---

## 8. FAKE-05 (rest): `FakeOutboxStore`

W1-7 (DCE-122, B-57) added the claim and the `limit` check. The rest, from `fakes/p05_outbox.py` and
`fake05.py` on this tree:

```
order of fetch (occurred_at m1=+10, m2=+5, m3=+0): ['m1', 'm2', 'm3']
duplicate message_id accepted: entries = ['m1', 'm1']
fetch after duplicate: [('m1', 0)]
mark_published(unknown) raises: no; is_published(ghost) = True
mark_failed(unknown): attempts = 1
```

- `fetch_unpublished` returns insertion order, not `occurred_at` order.
- `append` accepts a second entry with the same `message_id`; the claim, keyed by `message_id`, hides the second
  from fetches (`fake_outbox_store.py` l.124-134).
- `mark_published`, `mark_failed` accept ids never appended.

The `OutboxStore` Protocol (`outbox.py` l.92-235) says nothing about duplicate ids, unknown ids or order.
`message_id` is the integration event's `event_id` (F1.4), the id inbound dedup would see. The audit verdict
(`bug-audit-findings.md` l.227): "duplicate ids, marking unknown ids and insertion order are gaps".

---

## 9. Gaps and contradictions

1. ADR-062's "never incorrectly skipped" and the code's single `causation_id` key contradict each other
   (CQRS-05; §7).
2. ADR-058 says events have no pipeline behaviors; ADR-062 added them (A2).
3. The fake keeps results and marks in one key space; the how-to examples keep them apart and ask users to;
   `test_check_and_set_after_set_returns_true` pins the fake's sharing (§4).
4. The `ProcessedMessageStore` docstring names `EventBus` as the caller of `check_and_set` (§4).
5. An inbound domain event's `event_id` is not stable across redeliveries unless the translator copies it
   (F1.5); its `causation_id` is, when the payload has `correlation_id`.
6. In-process events are never redelivered by the library (F1.3); the only second delivery of an event is
   inbound, or a future F-10 dispatcher.
7. A failed handler cannot be redelivered through the inbound gateway today (F5.2, W2-3), so "mark only after
   success" has no observable effect on that path until W2-3 lands; it is observable through a direct
   `EventBus.dispatch` or a subscriber that redelivers on its own.
8. No registration name exists that would tell two handlers of one event type apart (F3.4).
