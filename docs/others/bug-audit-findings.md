# Bug and Logic-Error Audit: Verification Findings

| Item | Value |
|---|---|
| Date | 2026-10-21 |
| Baseline | branch `dev` at `35acef5` (0.3.0), Python 3.12.3, pydantic 2.13.5 |
| Plan | [`bug-audit-plan.md`](bug-audit-plan.md); row IDs, classes and severities are defined there |
| Fix plan | [`bug-fix-plan.md`](bug-fix-plan.md) |
| Method | Six verification agents, one per area, each trying to disprove its rows first, then reproducing them with throwaway probe scripts against the real classes and the shipped fakes |
| Status | Every row verified; audit tests (plan phases 1–2) not yet written; triage (phase 4) pending |

## Table of Contents

1. [Summary](#1-summary)
2. [S1 defects: fix first](#2-s1-defects-fix-first)
3. [Other confirmed defects](#3-other-confirmed-defects)
4. [Partly confirmed](#4-partly-confirmed)
5. [Contract gaps: the author decides](#5-contract-gaps-the-author-decides)
6. [By design, false positives, untestable](#6-by-design-false-positives-untestable)
7. [Corrections made to the plan](#7-corrections-made-to-the-plan)
8. [Docs and ADR findings](#8-docs-and-adr-findings)
9. [Probe scripts](#9-probe-scripts)

---

## 1. Summary

134 rows were verified: the plan's hypotheses, the downstream PD findings mapped into them, and 13 new
defects found while probing.

| Module | Confirmed | Partly | Gap | Design | False | Untestable | Rows |
|---|---|---|---|---|---|---|---|
| ddd | 12 | 4 | 4 | 1 | — | — | 21 |
| cqrs core | 23 | 3 | 5 | 1 | — | — | 32 |
| cqrs.saga | 18 | 6 | — | 1 | — | — | 25 |
| es | 10 | 5 | 6 | 2 | 1 | 1 | 25 |
| infrastructure | 17 | — | 1 | — | — | — | 18 |
| testing fakes | 8 | 2 | 2 | 1 | — | — | 13 |
| **Total** | **88** | **20** | **18** | **6** | **1** | **1** | **134** |

The pre-survey was accurate: only one row (ES-17) is a false positive, and six are decided by an ADR.
Twenty were narrowed: part of the claim held, part did not. Verification also lowered six severities and
raised one (§7).

**Most of the serious defects fall into four groups:**
- **Messages lost or duplicated with no error.** Inbound events are ACKed when their handler fails. Saga
  forward commands are dropped or compensated without being sent. Idempotency skips sibling events and
  lets duplicate commands through. Bootstrap starts consumers before handlers exist.
- **State leaking between commands.** A rollback does not drain repository event buffers, so the next
  command publishes a failed command's events. `FakeRepository` returns live objects.
  `FakeSagaRepository` re-emits stored events. Shared unit-of-work factories, which the docs teach, alias
  event lists across dispatches.
- **Serialization paths no test exercises.** Upcasting leaves a stale `event_version`, so the next read
  upcasts again (a value went from 50 to 500). One unknown legacy event stops every `EventStream` read.
  Snapshots restore nested models as dicts.
- **Fakes and docs that teach the bug.** Several fakes are looser than their Protocols. 69 docs lines call
  attributes that don't exist. Several how-tos produce the defect if followed literally (§8).

**Content in errors and logs** (§4.6 of the plan) is confirmed on every path the plan named:
- the outbox relay's stored errors;
- the dead-letter log;
- inbound validation logging;
- saga hydration warnings;
- `UpcastError`;
- the `repr()` of `OutboxEntry`, `GenericDomainEvent` and `CompensationRecord`.

Payload values appear in all of them. The default `LoggingBehavior` does not leak content; that sub-claim
was false.

---

## 2. S1 defects: fix first

These are the 39 S1 rows: silent loss, duplicate side effects, or lost or doubled messages. They are the
input to plan phase 1. "Evidence" quotes the probe output.

### 2.1 cqrs core and fakes

| ID | Defect | Evidence |
|---|---|---|
| CQRS-K1 | `IdempotencyBehavior` caches its result before the commit | Commit raised; the retry returned the cached result with the handler run once and no row stored |
| CQRS-K2 | `EventIdempotencyBehavior` marks the event processed before the handler | Handler failed, the event was redelivered: 1 call in total |
| CQRS-K3 | Locks are released before the commit | Order `acquire, handler, release, commit`. Two locked increments with an await in `_commit` gave `counter=1`: a lost update |
| CQRS-K5 | Rollback does not drain repository event buffers | With a repository that outlives the UoW (the docs' `FakeUnitOfWork(repository=repo)` wiring), handlers saw the rolled-back order |
| CQRS-04 | Two concurrent dispatches with one `command_id` both run | Documented slot order (idempotency outside locking): `handler runs=2`; with the lock outermost, 1 |
| CQRS-05 | Event idempotency is keyed by the command's id | Two `ItemAdded` from one command: the guarded handler saw only the first. Two handlers sharing a store: 1 ran. A store shared with `IdempotencyBehavior`: 0 ran. Contradicts ADR-062 ("never incorrectly skipped") |
| CQRS-08 | A shared UoW factory loses events in nested dispatch | `lambda: uow`, as `configure-command-bus.md:163` teaches, with nested dispatch: SKU-2 and SKU-3 silently dropped. The returned event list aliases the UoW's internal list |
| FAKE-01 | `FakeRepository` stores and returns the caller's object | Two loads are the same object; a stale second save raises no `ConcurrencyError`; an unsaved change is visible on the next load |
| FAKE-03 | Same root as CQRS-K5, seen through the fake | `save`, then a raise: the next commit publishes `['leaked']` |
| FAKE-11 | `FakeSagaRepository` has no version check | Two stale copies both save; the final state is the second writer's; saving version 0 over version 1 is accepted. Contradicts `saga/repository.py:34-35` |
| FAKE-12 | `FakeSagaRepository.save` copies before `pull_events()` | A second save with no new events re-emits the first event, with the same `event_id` |
| FAKE-13 | A failed command leaks into the next through `FakeRepository` | The next command sees `status: placed` without a save and publishes the failed command's event |

### 2.2 cqrs.saga

| ID | Defect | Evidence |
|---|---|---|
| SAGA-01 | `fail=True` drops the step's forward command | Bus received `ReserveItems, RefundPayment, CancelReservation`; `ChargePayment` was never sent. ADR-055's own headline example loses its command too |
| SAGA-03 | A terminal saga is re-suspended | COMPLETED → SUSPENDED and FAILED → SUSPENDED after a dispatch failure; `timeout_at` cleared |
| SAGA-04 | A saga suspended by a dispatch failure stays suspended | After a successful recovery: `status=SUSPENDED timeout_at=None`; `process_timeouts` never touches it. Stuck when no later allowed event arrives |
| SAGA-05 | A duplicate event resumes a suspended saga | Duplicate `e1`: `SUSPENDED → RUNNING`. A duplicate after recovery tripped the retry guard: `FAILED 'Retry limit exceeded'` |
| SAGA-06 | Timeout and event race | A late event after `timeout_at` completes the saga; racing both sent `CancelReservation` and `ConfirmOrder` |
| SAGA-07 | No optimistic concurrency, and the version jumps between saves | Versions per save `(None,0) (0,1) (1,1) (1,2) (2,4) (4,4) (4,5)`. With a version-checking repository the saga never got past PENDING, and no error surfaced |
| SAGA-08 | The manager swallows every exception | `handle()` returned `None` after a `ConcurrencyError`; a save failure after dispatch was recorded as "Dispatch failed" |
| SAGA-09 | Compensation has no checkpoint | A crash mid-compensation left the saga RUNNING with its stack intact; recovery dispatched nothing; redelivery re-sent every compensation |
| SAGA-11a | A pending command that fails hydration is deleted | Renamed module: `ReserveItems` lost, with no trace in `failed_compensations` or `error` |
| SAGA-12 | `on_timeout` commands have no checkpoint | A failed timeout command was lost; compensations queued before a raise were sent as forward commands, and `failed_compensations` stayed empty |
| SAGA-13 | Correlation is not unique (partly) | Two first events gave two saga rows with a yielding repository. `start_saga` twice gave 2 rows and `ReserveItems` twice. Events without `correlation_id` are dropped, against ADR-032 |
| SAGA-16 | Processed-event eviction is arbitrary (partly) | Newest id evicted in 70–91 of 200 trials; redelivering it re-sent `ReserveItems`. ADR-036's Decision says oldest |
| SAGA-19 | Wired to `CommandBus`, as annotated, the manager drops events | `ItemsReserved` never reached the saga: `confirmed=[]`; with `MessageBus` it did |
| SAGA-20 | Re-entrant lost update with `MessageBus` wiring | The nested save wrote COMPLETED, then the outer handler saved its stale RUNNING copy over it |

### 2.3 es

| ID | Defect | Evidence |
|---|---|---|
| ES-08 | Snapshots restore nested models as dicts (partly) | `nested field after snapshot load: dict`, then `AttributeError`. A computed field breaks every snapshot load (`property has no setter`). The removed-field case is by design (ADR-053) |
| ES-15 | Upcast chains stop at gaps and keep a stale version | Chain stopped at v2 with `event_version 1`; a round trip upcast twice (`n+v2+v2`). Contradicts `implement-upcaster.md:109` |
| ES-18 | Catch-up loses late-committed events (untestable here) | With a fake modelling out-of-order commits: the late event was lost and the next one delivered twice. Real for sequence-based stores; the protocol does not rule it out |
| ES-24 | The repository event buffer survives a rollback | The second commit published `['o2','o3']`: the failed command's event under the new command's IDs |
| ES-25 | `GenericDomainEvent` cannot be in an `EventStream` | `EventStream rejects GenericDomainEvent: ValidationError`, so one unknown legacy type stops `read_stream` and `read_all`, the crash ADR-044 set out to avoid |

### 2.4 infrastructure and ddd

| ID | Defect | Evidence |
|---|---|---|
| INF-01 | A failing inbound handler is ACKed | `handler calls: 1 subscriber outcome: ['ACK']`. With `EventIdempotencyBehavior` the redelivery is skipped as well |
| INF-02 | No dedupe key without `correlation_id` or `event_id` | No correlation: the handler ran twice with `causation_id None`. No `event_id`: a fresh uuid7 on each redelivery, handler ran twice |
| INF-05 | Consumers start before handlers exist | `acked = 1 nacked = 0 handled = 0`, with no log line |
| INF-10 | Payload values in stored errors and logs | The sentinel appeared in the `mark_failed` and dead-letter errors, the dead-letter ERROR log, inbound `logger.exception`, the `hydration.py:47` warning, and `UpcastError` |
| INF-14 | Upcasting in `EventRegistry` | Unregistered types are never upcast. A `KeyError` from a validator or an overridden `upcast()` is relabelled "Unregistered" and returned as `GenericDomainEvent`. Docs-style upcasters leave `event_version=1`: the second read fails, or a non-idempotent upcaster turns 50 into 500 |
| INF-16 | `bootstrap()` builds its own bus | `bootstrap(inbound_gateways=[gw])` without `message_bus=`: the app bus is not the gateway bus, a handler on the app bus was called 0 times, no error |
| DDD-01 | `model_copy()` shares pending events | Both directions: the copy's events appear on the original, and after the original drains, the copy still holds and re-publishes them |
| DDD-19 | The version rule is unspecified (gap) | The fake bumps on a no-op save (0 → 1 → 2); the SQLAlchemy how-to and the in-memory how-to each use a different rule. Three rules, no contract |

---

## 3. Other confirmed defects

S2 and S3, one line each; the plan rows hold the scenario.

**ddd:**
- DDD-03 (S2): NewType, PEP 695 alias and unparametrised generic ids raise `TypeError`, so a correct configuration cannot auto-generate.
- DDD-05 (S2): `model_validate(d)` writes `id` into `d`; reuse silently reuses the id.
- DDD-07 (S2): no assignment is validated; `id` reassignment breaks set membership, against `entity-identity.md:8`.
- DDD-09 (S3): the per-subclass generator is ignored by `DomainEvent`, and by `Command` too.
- DDD-10 (S2): `stamp("x", 123)` is accepted and the dump fails to revalidate; lists are shared with the original.
- DDD-11 (S3): a naive `occurred_at` is accepted, then comparison raises `TypeError`.
- DDD-13 (S2): frozen value objects with lists are mutable and unhashable; the how-to's `Money.multiply(-3)` returns -300 past its validator.
- DDD-14 (S3): composite specifications don't round-trip, although `specifications.md:32` promises serializable.
- DDD-16 (S3): a sync repository passes `isinstance(Repository)`, which the docs recommend for conformance.
- DDD-17 (S3): `import pydomain.ddd` loads 53 modules, including es, cqrs and infrastructure.
- DDD-20 (S3): a refused method keeps its change and its event; the docs' own `add_item` example does this.

**cqrs core:**
- CQRS-K4 (S2): `CQRSError` is a `DomainError`.
- CQRS-01 and CQRS-02 (S2): a cancel or failed release leaves a lock held. A failed release also rolls back a successful command.
- CQRS-06 (S2): rollback runs twice on every failure; a raising rollback loses the `CommandExecutionError`.
- CQRS-09 (S2): concurrent dispatches on a shared UoW cross-stamp each other's events.
- CQRS-11 (S3): `ctx.new_events` is always empty inside behaviors.
- CQRS-13 (S2): `ConcurrencyError` arrives wrapped, `isinstance(e, DomainError)` is true, and `IdempotentCommandIgnored` is never raised.
- CQRS-14 (S2): `OutboxEntry.causation_id` is the command id.
- CQRS-15 (S2): a dict holding a `datetime` passes primitive validation, then `json.dumps` fails.
- CQRS-16 (S2): two types on one topic produce a poison row.
- CQRS-17 (S2): subclass commands and events silently skip handlers, validators, locks and the outbox.
- CQRS-19 (S3): queries can't carry correlation.
- CQRS-21 (S3): `next()` called twice commits the failed run's event.
- CQRS-24 (S2): a clean exit without commit does not roll back.
- CQRS-25 (S3): a wrong translator class gives a poison row or silent field loss.
- CQRS-26 (S3): direct UoW use stamps `None` tracing ids.

**cqrs.saga:**
- SAGA-11b to 11e (S2): `retry_count` jumps 1 → 3; `max_retries=0` means unlimited on one path and immediate failure on the other; exhausted retries compensate on one path only; recovery reorders batches and overwrites causation.
- SAGA-17 (S2): any status transition is accepted, and `SagaStateError` is never raised.
- SAGA-21 (S2): `recover_pending_sagas` aborts the batch on the first failing saga.

**es:**
- ES-03 (S2): the fake records `command_id` before the version check, so the documented retry raises `DuplicateCommandError`.
- ES-04 and ES-05 (S3, fake): an empty append creates a stream; negative offsets slice from the end.
- ES-06 (S2): stored events have no tracing ids, against ADR-011.
- ES-09 (S2, fake): replay mutates the stored snapshot: items 5, 5, 5 for a 2-item stream.
- ES-10 (S2): a snapshot ahead of its stream makes every save fail.
- ES-14 (S3): `UpcasterRegistry` accepts duplicates, a missing target and downgrades.
- ES-23 (S3): same-name events collide in projections; a sync handler runs its side effect, then fails, so it is redelivered.

**infrastructure:**
- INF-03 (S2): registering a translation twice delivers twice with an appending subscriber.
- INF-06 and INF-07 (S2): partial start leaks consumers; one failing stop skips the rest; no shutdown test exists.
- INF-08 (S3): `event_store` is unused, and the registry has no accessor.
- INF-09 (S2): one failing `mark_failed` republishes earlier entries and skips later ones, breaking the `run_once` docstring.
- INF-11 (S2): `stop()` right after `start()` leaves the relay running; a hung publish blocks `stop()`.
- INF-12 (S3): `max_attempts <= 0` is accepted.
- INF-13 (S2): published events have no tracing ids, and the headers are empty.
- INF-15 (S2): payloads appear in `repr()`.
- INF-17 (S3): `Application` has no `message_bus` or `event_registry`.
- INF-18 (S3): `batch_size <= 0` is accepted.

**testing fakes:**
- FAKE-04 (S2): rollback keeps saves, and `_rolled_back` is never reset.
- FAKE-09 (S2): an unknown topic returns silently, against the fake's own docstring; coverage is 45 %.
- FAKE-10 (S2): ES-03, ES-04 and ES-09 inside the fakes.

---

## 4. Partly confirmed

| ID | Holds | Does not hold |
|---|---|---|
| DDD-04 | The `type(None)` branch is dead | `Optional` ids are generated correctly, so it is cleanup only |
| DDD-08 | `Sub.configure()` changes the base generator | The subclass override is documented in the `configure` docstring |
| DDD-15 | `and_` chains of 1,000 raise `RecursionError` | Subsumption is sound over 1,600 pairs, and every override points the right way |
| DDD-21 | `Specification[T]` raises; `is_satisfied_by` takes `Any` | `SpecificationError` is meant to be raised by users (`handle-domain-errors.md:58-64`) |
| CQRS-07 | Cancel after the database commit rolls back, loses events, and the rollback is unshielded | ADR-005:39 accepts in-process event loss without an outbox; what the database does with the rollback depends on the driver |
| CQRS-18 | A handler registered during dispatch runs in it | Cancellation behaves as normal asyncio; swallowing is ADR-046 (see A1) |
| CQRS-20 | A raising formatter fails the command; a cancel logs only "Processing" | The default `LoggingBehavior` logs no content |
| SAGA-02 | A handler raising after `dispatch()` still dispatches | The empty-stack `fail=True` case is ADR-055's design and is tested |
| SAGA-10 | COMPENSATING with an empty stack becomes COMPENSATED without dispatch | Reachable only from hand-built state |
| SAGA-13 | See §2.2 | A terminal saga blocking a new one is a gap; a unique constraint in a real database is untestable here |
| SAGA-14 | Two same-named sagas share one row, so the second never runs | Calling `bind_to` twice is deduplicated; subclass routing is a gap (A12) |
| SAGA-15 | Hydration runs module top-level code named by stored data, defaults renamed fields silently, and returns `None` for nested classes | Key stripping is ADR-033's design |
| SAGA-16 | See §2.2 | Pruning triggered from `record_step` is ADR-054's design; `prune_history` on COMPLETED is a gap |
| ES-02 | Events stored, not published, when the snapshot save fails | With a transactional store the rollback undoes the append; the worse effect is ES-24 |
| ES-07 | A `_when` that calls `_apply` double-buffers | A user error without a guard; the `GenericDomainEvent` sub-claim is really ES-25 |
| ES-08 | See §2.3 | Removed fields: ADR-053 |
| ES-12 | The `pending_event_count` doc is wrong | The modulo miss is ADR-043's explicit rule (a design gap) |
| ES-22 | `projection.checkpoint` diverges from `CheckpointStore` | `rebuild` leaving subclass state is documented |
| FAKE-05 | No claim; `limit <= 0` returns everything | Duplicate ids, marking unknown ids and insertion order are gaps |
| FAKE-07 | Releasing twice raises an undocumented `RuntimeError`; any task can release | `KeyError` is documented for unknown keys; no timeout is by design |

---

## 5. Contract gaps: the author decides

Real behaviour that nothing promises otherwise. Each needs a decision (document it, or change it with an ADR)
before its test can be written.

| ID | Decide |
|---|---|
| DDD-02 | Copy semantics of pending events across `copy`, `deepcopy`, `pickle` and `model_validate` (A11) |
| DDD-06 | Whether two entities with `id=None` may be equal |
| DDD-12 | Whether negative `version` and `event_version` are rejected |
| DDD-19 | The version rule for state-based saves (S1): who increments, and when |
| CQRS-03 | Lock reentrancy, or a documented ban on same-key nested dispatch |
| CQRS-10 | Whether `commit()` may be called twice |
| CQRS-12 | How a Protocol-only UoW receives tracing ids |
| CQRS-22 | Whether projections get a re-apply guard (ADR-048 says they must be idempotent) |
| CQRS-23 | Event collection order |
| ES-01 | Whether an aggregate instance is spent after a failed save (the docs say reload) |
| ES-05 | Validation of negative offsets |
| ES-11 | A snapshot without a stream: resurrect, or return `None` |
| ES-16 | A round-trip fake for the ES load path |
| ES-19 | Poison events in subscriptions: retry limit, dead letter, backoff |
| ES-21 | More than one subscription runner |
| INF-04 | Exact-type vs subclass dispatch (A12) |
| FAKE-02 | Version on insert |
| FAKE-08 | Broker fake lifecycle and copying |

---

## 6. By design, false positives, untestable

| ID | Verdict | Why |
|---|---|---|
| DDD-18 | Design | ADR-006 Negative: hashing by `id` alone collides across types, "mitigated by `__eq__`". Docs only |
| CQRS-27 | Design | ADR-046 chose full tracebacks; it conflicts with §4.6, so decide with A1 |
| SAGA-18 | Design | ADR-056 blocks unlisted events; the doc drift is A35 |
| ES-13 | Design | ADR-053 §4: "Available for users who want explicit error handling instead of silent fallback" |
| ES-20 | Design | ADR-048: "projections must be idempotent" |
| FAKE-06 | Design | The protocol defines one key space; the defect is CQRS-05 |
| ES-17 | **False** | `read_all` promises "all global events from `from_version` onward", so the checkpoint is right under the protocol. Hardening is cheap: checkpoint `stream_start + len(stream.events)` |
| ES-18 | Untestable | Needs a database with a sequence; reproduced with a fake that models late commits (§2.3) |

---

## 7. Corrections made to the plan

**Severity changes:**
- Lowered: CQRS-01 and CQRS-02 (S1 → S2; a hang, not loss); ES-01 (S1 → S3; the docs say reload);
  ES-02 (S1 → S2); ES-10 (S1 → S2; it raises); INF-09 (S1 → S2; duplicates, which ADR-063 accepts);
  INF-04 (S2 → S3; loud); DDD-06 (S2 → S3); SAGA-02 (S1 → S2); SAGA-10 (S2 → S3); ES-17 (S1 → S3, false).
- Raised: INF-14 (S2 → S1; silent value change on re-read).

**Text and references:**
- CQRS-K4 is at `exceptions.py:10`.
- CQRS-15's validator bypass is at `integration_events.py:93`.
- CQRS-K5, FAKE-03 and ES-24 share one root: `AbstractUnitOfWork.rollback` (`unit_of_work.py:150-161`).
- INF-14's duplicate-name claim was dropped: `register` raises on duplicates.
- NEW-F-2 (`find_stalled_sagas` returns COMPENSATING) is a docs defect, not a fake defect (A29).
- Several TEST rows were overstated and narrowed (plan §11).

One verification agent reported different `unit_of_work.py` line numbers; they were checked against the
file (245 lines) and rejected.

---

## 8. Docs and ADR findings

Plan §12 holds all of them with a Verified column:
- **ADR conflicts A1–A13 and A40:** A10 turned out to be stale test names, not a conflict. A40 is new:
  ADR-037 against ADR-044.
- **Docs that teach a defect, A14–A37:** A22–A37 were found during verification. The most harmful:
  - A26: 69 lines use `app.message_bus` or `app.event_registry`;
  - A27: the inbound how-to loses events;
  - A30: the upcaster examples cause INF-14;
  - A33: `lambda: uow` causes CQRS-08;
  - A29: the stalled-saga contract, where an adapter written to the docs never recovers compensations;
  - A25: the checkpoint how-to re-applies events after a restart.

---

## 9. Probe scripts

The probes live outside the repository, in this session's scratchpad, and will not survive it:
`/tmp/claude-1000/-home-mgourlis-projects-recollections/f4066b9e-d8c1-4775-a218-9a77afbde28e/scratchpad/verify/{ddd,cqrs,saga,es,inf,fakes}/`.

Each one runs with:

```bash
env -u VIRTUAL_ENV PYTHONPATH=<probe dir> uv --directory /home/mgourlis/projects/pydomain run --frozen python <probe>
```

They use synthetic data only. Phase 1 should turn each S1 probe into a strict-xfail test in the module's
test directory. That test, not the probe, is the durable evidence.
