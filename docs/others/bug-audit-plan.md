# Bug and Logic-Error Audit Plan

| Item | Value |
|---|---|
| Status | Plan; every hypothesis verified by probe on 2026-10-21 (§1.1); audit tests not yet written |
| Date | 2026-10-21 |
| Baseline | branch `dev` at `35acef5` (0.3.0) |
| Scope | `ddd`, `cqrs` (core), `cqrs.saga`, `es`, `infrastructure`, `testing` fakes, and the test suite of each |
| Output | Failing tests for every confirmed defect, the findings report [`bug-audit-findings.md`](bug-audit-findings.md), one DCE issue per fix |

## Table of Contents

1. [Why an audit, and why this method](#1-why-an-audit-and-why-this-method) ([1.1 Verification](#11-verification-2026-10-21))
2. [Method](#2-method)
3. [Shared tooling (phase 0)](#3-shared-tooling-phase-0)
4. [Cross-cutting checks](#4-cross-cutting-checks)
5. [Module: ddd](#5-module-ddd)
6. [Module: cqrs core](#6-module-cqrs-core)
7. [Module: cqrs.saga](#7-module-cqrssaga)
8. [Module: es](#8-module-es)
9. [Module: infrastructure](#9-module-infrastructure)
10. [Module: testing fakes](#10-module-testing-fakes)
11. [The test suite itself](#11-the-test-suite-itself)
12. [ADR conflicts and drift to resolve](#12-adr-conflicts-and-drift-to-resolve)
13. [Downstream findings (PD1–PD51)](#13-downstream-findings-pd1pd51)
14. [Order of work, agents and timeboxes](#14-order-of-work-agents-and-timeboxes)
15. [Exit criteria](#15-exit-criteria)

---

## 1. Why an audit, and why this method

The suite is large and green: **1,757 tests pass in 7.4 s with 96.65 % branch coverage**
(`pytest`, 2026-10-21). Coverage does not include `__init__.py` files (`[tool.coverage.run] omit`),
so the eager `pydomain.es` import in `pydomain/__init__.py` is never measured.

High coverage has not prevented the defects found so far. A downstream review of 0.3.0 confirmed:
- `IdempotencyBehavior` caches its result before the commit.
- Locks are released before the commit.
- `CQRSError` inherits from `DomainError`, contrary to ADR-004.
- A rollback does not drain buffered events.
- `EventIdempotencyBehavior` marks a message processed before its handler runs.

Every one of these lines is covered. The bugs sit in **ordering, failure paths, cancellation, concurrency,
copy semantics, and fakes that are more permissive than the Protocols they stand in for**. The library's
own tests run against those fakes, so they cannot catch a defect the fakes hide.

The audit therefore does not chase coverage. For each module it:
1. lists the contracts (README, docstrings, ADRs);
2. turns each risk hypothesis into a test written against the contract, not the implementation;
3. runs the hypothesis under injected failures, cancellation and concurrency;
4. classifies the result.

The pre-survey for this plan was five read-only agents, one per area, on 2026-10-21. It produced about
100 hypotheses, listed per module below with the downstream findings added (§13).

### 1.1 Verification (2026-10-21)

Every row was then checked by six verification agents, one per area. Each tried to disprove its rows first,
and reproduced each one with a throwaway probe script against the real classes and the shipped fakes. Rows
that needed a real database or SQLAlchemy were checked by reading. The Status column now holds the verdict:

| Verdict | Meaning | Rows |
|---|---|---|
| **confirmed** | Reproduced, and it breaks a stated contract (ADR, README, docstring or doc page) | 88 |
| **partly** | Some sub-claims reproduced, others were wrong or by design; the findings report says which | 20 |
| **gap** | The behaviour is real, but nothing promises otherwise; the author decides (§2.3 "Contract gap") | 18 |
| **design** | An ADR decides it and its reasoning covers the case | 6 |
| **false** | Did not reproduce, or the protocol rules the scenario out | 1 |
| **untestable** | Needs a real database; reproduced only with a fake that models it | 1 |

A `PDnn` tag means the downstream review reported the same defect (§13). Severities were corrected where
verification showed a different outcome (e.g. a hang, not data loss). The evidence for every row, the
false positives and the corrections are in [`bug-audit-findings.md`](bug-audit-findings.md). The audit
tests in phases 1–2 still have to be written: a probe shows the defect exists, a test keeps it fixed.

Line numbers refer to `35acef5`; find code by its symbol if the lines have moved.

---

## 2. Method

### 2.1 Workflow for each hypothesis

```
contract (ADR / README / docstring)
  → test written against the contract, in the module's own test directory
  → run: passes?  → close as "not reproducible" (keep the test if it guards a real risk)
         fails?   → mark xfail(strict=True, reason="AUD-<area>-NN") and classify
  → triage with the author → DCE issue → fix branch → remove the xfail marker
```

`xfail(strict=True)` lets red tests merge before their fix without breaking CI. When a fix lands, the
test starts passing, strict mode fails the run, and that forces the marker's removal. Every audit test
becomes a permanent regression test. There is no separate `tests/audit/` folder.

### 2.2 IDs

`AUD-<area>-NN`, with these area codes:
- `DDD`
- `CQRS`
- `SAGA`
- `ES`
- `INF`
- `FAKE`
- `TEST` (defects in the test suite itself)
- `X` (cross-cutting)

The IDs in the tables below are provisional. Keep them stable once a test exists.

### 2.3 Classification

| Class | Meaning | Outcome |
|---|---|---|
| **Bug** | Violates a stated contract (ADR, README, docstring) | Fix in 0.3.x if non-breaking; otherwise schedule with a superseding ADR |
| **Contract gap** | No ADR or doc decides the behaviour (e.g. copy semantics of pending events) | Author decides; record it in an ADR or a docstring, then test the decision |
| **ADR conflict** | Two ADRs, or an ADR and the code, disagree (§12) | Author decides which wins; supersede or amend the ADR (accepted ADRs are never edited) |
| **Doc drift** | Code is fine; an ADR or doc describes it wrongly | Docs fix |
| **By design** | An ADR decides it and the reasoning holds | Close; cite the ADR in the test or the findings |
| **Not reproducible** | The test passes | Close; keep the test only if it guards a real risk |

### 2.4 Severity

| Severity | Meaning | Examples |
|---|---|---|
| **S1** | Silent data loss, a duplicate side effect, or a lost or double message, with no error raised | A forward command dropped; an event ACKed and lost; a checkpoint skipping events |
| **S2** | A contract violation that raises, or that a careful caller can detect and recover from | Wrong exception type; double rollback masking the cause |
| **S3** | A footgun, missing validation, or misleading API | Negative versions accepted; silent overwrite on duplicate registration |

S1 is fixed before anything else, in any module.

### 2.5 Rules for audit tests

- Assert the **contract**, never private state. No `_committed`, `_behaviors` or `_store` asserts in new
  tests. Observe behaviour through the public API, spies, or the contract suites (§3).
- One hypothesis per test, named after the behaviour: `test_lock_released_when_acquire_is_cancelled`,
  not `test_aud_cqrs_01`. The AUD ID goes in the xfail reason and in the findings report.
- Synthetic data only: order, invoice and shipment style examples, as the existing tests use.

---

## 3. Shared tooling (phase 0)

This is built once, before any module work, in `tests/support/`. The contract suites live in
`src/pydomain/testing/contracts/`, so that adapters outside the library can run them as well.

| Tool | Purpose | Notes |
|---|---|---|
| `hypothesis` (dev dep) | Property and stateful (`RuleBasedStateMachine`) tests | add to the `[dev]` extra in `pyproject.toml` |
| `mutmut` (dev dep) | Mutation testing on core modules (§4.8) | The suite runs in 7 s, so mutation runs are cheap |
| **Fault injector** | A wrapper around any async Protocol object that raises `Exception`, `CancelledError` or `SystemExit` at the Nth awaited call | Used for UoW, repositories, stores, lock provider, broker, bus |
| **Ordering spy** | A single list recording `flush`, `write_outbox`, `commit`, `rollback`, idempotency `set`, lock `release` and `publish` across collaborators | Lets tests assert ADR-005/020 sequences |
| **Round-trip fakes** | Variants of the fakes that store `model_dump_json()` and load with `model_validate_json()` | Removes reference aliasing; exercises serialization, the registry and upcasters on the load path |
| **Version-checking fakes** | A FakeRepository and FakeSagaRepository that store copies and compare the stored version with the caller's expected version | Makes two loads plus two saves conflict, as a real store would |
| **Leaked-task fixture** | An autouse fixture that fails a test leaving tasks or held locks behind | Catches cancellation leaks |
| **CI matrix** | Run the suite, and the audit tests, on Python 3.12, 3.13 and 3.14 | The classifiers claim 3.12–3.14 but CI runs 3.12 only (PD48). Do this first |
| **anyio backends** | Run concurrency and cancellation tests on asyncio, and on trio if trio support is claimed | `conftest.py` is asyncio-only today; decide the claim first |
| **Import probe** | A subprocess helper: `import X`, return `sys.modules` | For the import-isolation checks (§4.7) |
| **Content sentinel** | A fixed synthetic string placed in payloads; asserts it is absent from `caplog` and from persisted `error` fields | For the content-in-errors checks (§4.6) |

The contract suites run against every implementation of each Protocol: the shipped fakes now, and any
adapter later. One parametrised suite per Protocol:
- `Repository`
- `UnitOfWork`
- `EventStore`
- `SnapshotStore`
- `CheckpointStore`
- `OutboxStore`
- `ProcessedMessageStore`
- `LockProvider`
- `SagaRepository`
- `MessageBroker`
- `MessageSubscriber`
- `ProjectionStore`

Each suite states the contract once, so a fake cannot drift from it unnoticed.

The project installs with pip (`make install`, CI), a deliberate choice (`433c6d7`); a stale `uv.lock` is still
in the tree. Every agent uses pip and `make`, so all runs share CI's environment.

---

## 4. Cross-cutting checks

These checks apply to every module. The module sections list only their specific hypotheses.

### 4.1 Failure at every await
For each public operation that awaits collaborators, inject a failure at await 1..N. Each operation should
end in exactly one of two states: fully applied, or fully rolled back. The original cause must be
preserved, and no lock, task, buffered event or outbox row may leak.

### 4.2 Cancellation
Repeat 4.1 with `CancelledError`, which is a `BaseException`, so `except Exception` blocks do not catch
it. Check three things:
- Cleanup (rollback, lock release) is shielded.
- A cancellation after the database commit is not reported as a failure, and does not roll back.
- Cancelling one handler does not silently drop the rest of a batch, unless that is documented.

### 4.3 Concurrency
Run two operations in a task group with a forced yield between their steps:
- the same aggregate;
- the same `command_id`;
- the same lock key;
- the same saga correlation;
- two relays on one outbox;
- two subscription runners.

Exactly one of the two should win, or both should be safely serialised. Shared-instance factories
(`lambda: uow`), which the tests use throughout, must be checked separately (§6, R8–R9).

### 4.4 Copy and serialization semantics
For every class with private state (`AggregateRoot._pending_events`, saga state, snapshots), run the
matrix: `model_copy`, `model_copy(deep=True)`, `copy.deepcopy`, `pickle`, `model_validate(model_dump())`
and JSON round trip. Decide the intended behaviour of each (a contract gap today), then test it.

### 4.5 Type dispatch and identity
Several modules dispatch on the exact `type(x)` or identify classes by `__name__`:
- the event bus;
- the outbox registry;
- the validators;
- the lock key providers;
- the event registry;
- the saga registry;
- hydration.

For each, test a subclass and two same-named classes from different modules. Decide whether to support
the subclass, or to refuse it loudly, and record the decision.

### 4.6 Content in errors and logs
The library carries user payloads, and an error string built from a Pydantic `ValidationError` includes
`input_value`. Using the content sentinel, check that payload values appear in none of these:
- exception messages;
- logs;
- persisted `error` fields (outbox `mark_failed` and `mark_dead_lettered`);
- `UpcastError` text.

Today they do (see INF-10). This matters to any user of the library who handles personal data.

### 4.7 Import isolation and layering
In a subprocess, check:
- `import pydomain.ddd` does not load `pydomain.cqrs`, `pydomain.es` or any third-party module other
  than pydantic and uuid-utils;
- `import pydomain.cqrs` does not load `pydomain.es`;
- `import pydomain.infrastructure.message_bus` does not load `pydomain.es`.

All of these fail today, because of the eager imports in `pydomain/__init__.py:76`,
`infrastructure/__init__.py:22` and `bootstrap.py:17-18`. Add `-X importtime` as a soft budget. Extend
the pytest-archon rules (§11).

### 4.8 Mutation testing
Run `mutmut` on the modules where a wrong comparison or a swapped statement changes semantics:
- `ddd/entity.py`, the id guard and equality;
- `ddd/specification.py`;
- `cqrs/behaviors.py`;
- `cqrs/unit_of_work.py`;
- `cqrs/command_bus.py`;
- `cqrs/outbox.py`;
- `cqrs/saga/saga.py` and `cqrs/saga/state.py`;
- `es/event_sourced_repository.py`, `es/snapshot.py` and `es/upcasting.py`;
- `infrastructure/message_broker.py` and `infrastructure/subscription.py`.

Each surviving mutant is either a missing test or dead code. Don't set a score target in this audit;
record the score as a baseline.

### 4.9 Typing contracts
Add `tests/typing/`, checked by `mypy --strict`, with `assert_type` for:
- `Repository[Order, UUID]` inference;
- `stamp() -> Self` on subclasses;
- `CommandBus` result types;
- a Factory return mismatch that mypy should reject.

This catches regressions in the generics promised by ADR-015 and ADR-029. Include the typing defects the
downstream review found:
- The result type is not tied to `Command[R]`, and renamed handler parameters are rejected because the
  protocol's parameters are not positional-only (PD44).
- `dispatch` returns a tuple or `Any`, despite ADR-015 (PD45).
- Validators and lock-key resolvers are typed `Any` (PD47).
- `Specification[T]` raises (DDD-21).

---

## 5. Module: ddd

**Contracts:** ADR-006 (identity), ADR-007 (auto id with runtime guard), ADR-008 (UUIDv7), ADR-009
(domain service), ADR-010 (specification), ADR-011 (event stamp immutability), ADR-013 (factory vs
reconstitution), ADR-050 (pending events private attribute), README: zero infrastructure imports.

| ID | Where | Scenario | Check | Status | Sev |
|---|---|---|---|---|---|
| DDD-01 | `aggregate_root.py:27` | `model_copy()` shares the `_pending_events` list, so an event added to the copy appears on the original and can be published twice | §4.4 matrix | **confirmed** · PD3 | S1 |
| DDD-02 | `aggregate_root.py:27` | `deepcopy` and `pickle` keep pending events while `model_validate(dump)` drops them, so a cloned aggregate re-emits them | §4.4 matrix | **gap** | S2 |
| DDD-03 | `entity.py:58` | `Entity[NewType(...)]`, or an unresolved generic subclass, raises a raw `TypeError` from `isinstance`, not `DomainError` (ADR-007). A PEP 695 `type XId = UUID` alias fails the same way | parametrised id types | **confirmed** · PD2 | S2 |
| DDD-04 | `entity.py:52` | `origin is type(None)` is never true, so the branch is dead and `Optional` ids fall through | mutation plus test | **partly** | S3 |
| DDD-05 | `entity.py:65` | `model_validate(d)` writes `id` into the caller's dict; reusing the dict silently reuses the id | test | **confirmed** | S2 |
| DDD-06 | `entity.py:81` | With `Entity[UUID \| None]`, two new entities with `id=None` are equal and collapse in a set | property: equivalence relation | **gap** | S3 |
| DDD-07 | `entity.py:36,83` | The id is mutable without validation (`e.id = "x"`), which breaks hash, set and dict membership. `test_can_change_id` blesses it | decide: freeze id (after a deprecation warning) or validate | **confirmed** · PD36 | S2 |
| DDD-08 | `entity.py:76` | `Sub.configure(...)` replaces the global generator, and a subclass's own `_id_generator` silently ignores it | test | **partly** | S3 |
| DDD-09 | `domain_event.py:38` | The event id factory reads `DomainEvent._id_generator`, so a per-subclass override is ignored | test | **confirmed** · PD51 | S3 |
| DDD-10 | `domain_event.py:53` | `stamp()` uses `model_copy(update)`, so `correlation_id="x"` is accepted and mutable payload fields are shared | test | **confirmed** | S2 |
| DDD-11 | `domain_event.py:39` | `occurred_at` accepts naive datetimes, so UTC is only the default, not an invariant. The clock is inconsistent: the relay and fake outbox take one, while events, sagas and snapshots call `datetime.now(UTC)` | decide plus test | **confirmed** · PD40 | S3 |
| DDD-12 | `entity.py:34`, `domain_event.py:40` | `version` and `event_version` accept negative values | test | **gap** | S3 |
| DDD-13 | `value_object.py:15` | A ValueObject with a `list` field is "frozen" but mutable in place and unhashable. `model_copy(update)`, the README Money pattern, skips validation; the README, quickstart and value-object how-to all teach it | test plus docs review | **confirmed** · PD4, PD37 | S2 |
| DDD-14 | `specification.py:111,126,141` | `model_dump()` of a composite drops subclass fields, and there is no round trip | decide: supported or not | **confirmed** | S3 |
| DDD-15 | `specification.py:85-132` | Subsumption: `s.subsumes(s)` is False; check the direction of every override; deep `and_` chains risk recursion depth | property: `x.subsumes(y)` implies `sat(y) ⊆ sat(x)` | **partly** | S3 |
| DDD-16 | `repository.py:16` | `runtime_checkable` accepts a synchronous repository (ADR-003); `TId` is not tied to the aggregate's id type | test plus typing test | **confirmed** | S3 |
| DDD-17 | `pydomain/__init__.py:76` | Importing `pydomain` (so any `pydomain.*` module) loads `pydomain.es` | §4.7 | **confirmed** · PD13 | S3 |
| DDD-18 | entity equality | A parent and a child class with the same id are unequal but have equal hashes. Legal, but decide and document it | decide plus test | **design** | S3 |
| DDD-19 | `repository.py:38-47`, `FakeRepository.save` | The version rule is unspecified. The fake increments on every update, even one that changed nothing; SQLAlchemy's `version_id_col` bumps only changed rows. A guard that relies on the bump passes in tests and does nothing in a database. The state-based and event-sourced rules (ADR-041) differ and need stating separately | Repository contract suite | **gap** · PD5 | S1 |
| DDD-20 | `aggregate_root.py:29-44`, `concepts/ddd/aggregates.md:13,81` | A method that changes a field and then raises keeps both the change and the buffered event, while the docs say invariants hold after every mutation | decide: docs fix or an optional helper | **confirmed** · PD21 | S3 |
| DDD-21 | `specification.py:16,34` | `Specification[T]` raises `TypeError`, `is_satisfied_by` takes `Any`, and `SpecificationError` is never raised, although ADR-010 chose the hybrid for type safety | §4.9 typing test | **partly** · PD41 | S3 |

**Property tests:**
- Entity equality is an equivalence relation, and equal entities have equal hashes.
- ValueObject equality matches `model_dump` equality.
- Specification laws: De Morgan, double negation, and `And`/`Or` match `all`/`any` over generated
  domains.
- `stamp()` changes only the two tracing fields.
- `pull_events()` is idempotent.

**Test-suite work:**
- Flip `test_can_change_id` once DDD-07 is decided.
- Rename `test_int_entity_requires_id` and `test_str_entity_requires_id`. They pass only through the
  mismatch guard, while ADR-007 says ids are generated.
- Give `test_explicit_id_preserves_auto_generated_flag` a real assert.
- Move every global `configure` test into a fixture that always restores the generator.

---

## 6. Module: cqrs core

Scope: commands, queries, handlers, command_bus, query_bus, event_bus, behaviors, unit_of_work,
idempotency, locking, outbox, integration_events, projection, exceptions.

**Contracts:**
- ADR-004: exception layers.
- ADR-005: publish after commit.
- ADR-014 to ADR-026: messages, handlers, pipeline, idempotency, locks, UoW ownership, tracing,
  integration events, projections.
- ADR-041: idempotency and concurrency.
- ADR-046 and ADR-062: event bus.
- ADR-061: tracing.
- ADR-063 to ADR-065: outbox.

### 6.1 Regression targets (known)

| ID | Where | Defect | Status | Sev |
|---|---|---|---|---|
| CQRS-K1 | `behaviors.py:319-324` | `IdempotencyBehavior` caches the result before the commit, so a lost write is reported as success | **confirmed** · PD17 | S1 |
| CQRS-K2 | `behaviors.py:368` | `EventIdempotencyBehavior` marks the message processed before the handler runs, keyed by `causation_id`, so a failed handler is never retried | **confirmed** · PD50 | S1 |
| CQRS-K3 | `behaviors.py:284-288` | Locks are released before the commit | **confirmed** · PD18 | S1 |
| CQRS-K4 | `exceptions.py:10` | `CQRSError` inherits from `DomainError`, contrary to ADR-004 | **confirmed** · PD19 | S2 |
| CQRS-K5 | `unit_of_work.py:150-161` | A rollback does not drain buffered events, so the next command publishes them | **confirmed** · PD22 | S1 |

### 6.2 New hypotheses

| ID | Where | Scenario | Check | Status | Sev |
|---|---|---|---|---|---|
| CQRS-01 | `behaviors.py:279` | Cancellation while waiting on lock 2 leaves lock 1 held, because `except Exception` misses `CancelledError` | §4.2 | **confirmed** | S2 |
| CQRS-02 | `behaviors.py:280-288` | One failing `release()` aborts the loop, so later locks stay held | fault injector on LockProvider | **confirmed** | S2 |
| CQRS-03 | lock behavior | A nested dispatch with the same lock key deadlocks on a non-reentrant lock | test with timeout | **gap** | S2 |
| CQRS-04 | `behaviors.py:319-324` | Two concurrent dispatches with the same `command_id` both miss the cache and both execute, because `get` then `set` is not atomic; `check_and_set` exists but is unused here | §4.3 | **confirmed** · PD17 | S1 |
| CQRS-05 | `command_bus.py:123`, `behaviors.py:368` | Idempotency keys collide: every event from one command has `causation_id == command_id`, so the second event, a second handler, or a store shared with `IdempotencyBehavior` is skipped | test with two events and two handlers | **confirmed** | S1 |
| CQRS-06 | `command_bus.py:155`, `unit_of_work.py:121-122` | When `commit()` raises, rollback runs twice. A non-idempotent rollback raises and masks the cause; a raising first rollback leaks a raw error instead of `CommandExecutionError` | ordering spy plus fault injector | **confirmed** · PD22 | S2 |
| CQRS-07 | `unit_of_work.py:115-148` | Cancellation mid-commit: the rollback in `__aexit__` is not shielded. A cancel after the database commit leaves `_committed=False`, so the UoW rolls back a committed transaction and loses in-process events | §4.2 | **partly** | S2 |
| CQRS-08 | `unit_of_work.py:112,167-169` | `collect_events()` returns the internal list and `__aenter__` clears it. With a shared UoW factory, a nested dispatch empties the list `dispatch_many` is iterating | test with a shared factory | **confirmed** | S1 |
| CQRS-09 | `command_bus.py:130` | Concurrent dispatches on one UoW overwrite `_correlation_id` and clear each other's events | §4.3 | **confirmed** | S2 |
| CQRS-10 | `unit_of_work.py:213,245` | Retrying `commit()` without a rollback rewrites every accumulated event to the outbox, creating duplicate rows | test | **gap** | S3 |
| CQRS-11 | `command_bus.py:148` | `ctx.new_events` is set after the pipeline returns, so no behavior can see it | test, or remove it | **confirmed** | S3 |
| CQRS-12 | command_bus stamping | A UoW that satisfies only the Protocol, without the abstract base, has its events silently left unstamped | contract suite | **gap** | S2 |
| CQRS-13 | `command_bus.py:156` | `ConcurrencyError` is wrapped in `CommandExecutionError`, so ADR-041 retry logic must dig into `__cause__`. `IdempotentCommandIgnored` is never raised | decide plus test | **confirmed** · PD19 | S2 |
| CQRS-14 | `outbox.py:342` | `OutboxEntry.causation_id` is the command id, while ADR-061 says the domain `event_id` | test | **confirmed** | S2 |
| CQRS-15 | `integration_events.py:63-70,93`, `outbox.py:338` | Primitive validation is shallow: `{"at": datetime}` passes and fails later at JSON serialization; `stamp()` skips the validator | property test | **confirmed** | S2 |
| CQRS-16 | `outbox.py:278` | Two domain types on one topic overwrite `_by_topic`, so old rows rehydrate as the wrong class | test | **confirmed** | S2 |
| CQRS-17 | `event_bus.py:108`, `outbox.py:327`, `behaviors.py:237`, `locking.py:68` | Exact-type dispatch: subclass events, commands and validators are silently unhandled or unvalidated | §4.5 | **confirmed** | S2 |
| CQRS-18 | `event_bus.py:108-125` | A handler registered during dispatch runs in the same dispatch. `CancelledError` aborts the remaining handlers (ADR-046). Swallowed failures hide errors from at-least-once callers (§12) | tests | **partly** | S3 |
| CQRS-19 | `query_bus.py:77-78` | Queries ignore ambient tracing: correlation is always `query_id` | decide plus test | **confirmed** | S3 |
| CQRS-20 | LoggingBehavior | It logs nothing on cancellation; a raising `payload_formatter` fails the command; the default can log content | §4.6 | **partly** | S3 |
| CQRS-21 | pipeline | A behavior calling `next()` twice re-runs the handler in the same UoW with the first run's events still buffered | decide: guard or document | **confirmed** | S3 |
| CQRS-22 | projection | Nothing tests re-applying the same event to a projection | contract suite | **gap** | S3 |
| CQRS-23 | `unit_of_work.py:239-240` | Events are collected in repository registration order, not in the order they were raised; nothing promises either | decide plus document; optional sequence number | **gap** · PD7 | S3 |
| CQRS-24 | `unit_of_work.py:121` vs the protocol docstring `:31-32` | A clean exit without `commit()` does not roll back, though the protocol says it does | UnitOfWork contract suite | **confirmed** · PD22 | S2 |
| CQRS-25 | `outbox.py` `to_entry` | The translator's return value is never checked against the registered integration class | test | **confirmed** · PD26 | S3 |
| CQRS-26 | `unit_of_work.py:241-244` | A UoW committed without the CommandBus stamps `None` tracing IDs: `stamp()` is typed `UUID` but accepts `None`, against ADR-011 ("both IDs are populated") | test | **confirmed** | S3 |
| CQRS-27 | `event_bus.py:121` | A failing event handler is logged with `logger.exception`, so the exception text and traceback reach the log; an error message built from payload values leaks them. ADR-046 chose full tracebacks; conflicts with §4.6 | decide plus §4.6 sentinel | **design** · PD25 | S3 |

**Stateful tests:** outbox and relay as a `RuleBasedStateMachine`. Rules: append, fetch, publish succeeds
or fails, crash mid-batch, advance the clock. Invariants:
- An entry is never both published and dead-lettered.
- Attempts never exceed `max_attempts`.
- Every entry eventually reaches published or dead-lettered.
- Backoff is monotone and capped.

**Ordering tests:** these assert the ADR-005/020 sequence of flush, write outbox, commit, idempotency
`set`, lock release, then publish. They fail today (K1, K3). Write them first, as strict xfail.

**Test-suite work:**
- `test_pipeline.py:84-102` asserts `_behaviors`; `test_bus.py:67-318` asserts `uow._committed` and
  `_rolled_back`. Move both to spies.
- `tests/cqrs/test_exceptions.py:19,49,85` pin the K4 inheritance. Flip them with the fix.
- Add per-test UoW factories, so shared instances stop hiding CQRS-08 and CQRS-09.

---

## 7. Module: cqrs.saga

Scope: saga, state, manager, registry, repository, hydration, pruning, and `testing/fake_saga_repository.py`.

**Contracts:**
- ADR-027: state as aggregate.
- ADR-028, ADR-055: the `on()` DSL and `fail=True`.
- ADR-029, ADR-030: generics and registry.
- ADR-031: manager.
- ADR-032: correlation.
- ADR-033: LIFO compensation.
- ADR-034: suspension and timeout.
- ADR-035: crash recovery.
- ADR-036: processed events.
- ADR-054: pruning.
- ADR-056: resume authorization.
- ADR-057: the default timeout sentinel.

| ID | Where | Scenario | Check | Status | Sev |
|---|---|---|---|---|---|
| SAGA-01 | `saga.py:261-268`, `saga.py:333,447` | `fail=True` dispatches the forward command, pushes its compensation, then `fail()` → `execute_compensations()` clears the forward command. The step's command is compensated but never sent | test through the bus | **confirmed** | S1 |
| SAGA-02 | `manager.py:309,318` | With an empty compensation stack, `fail()` sets FAILED, yet the manager still dispatches the queued forward commands. The same happens when a handler raises after `dispatch()` | test | **partly** | S2 |
| SAGA-03 | `manager.py:233` | A dispatch failure calls `suspend()` without a status check, so a COMPLETED or FAILED saga becomes SUSPENDED and `timeout_at` is cleared | stateful test | **confirmed** | S1 |
| SAGA-04 | `manager.py:450-453` | A saga suspended after a dispatch failure has no timeout, and a successful re-dispatch never resumes it, so it stays SUSPENDED forever | test | **confirmed** | S1 |
| SAGA-05 | `manager.py:286-294`, `saga.py:102` | Redelivering the event that caused a suspension resumes the saga before the processed-event check. A duplicate also overwrites `causation_id` and can trip the retry guard | test | **confirmed** | S1 |
| SAGA-06 | handle path vs `process_timeouts` | An event arriving at or after `timeout_at` is handled without a timeout check, contrary to ADR-034. Timeout and event both load, mutate and save, and the last writer wins | §4.3 plus a version-checking fake | **confirmed** | S1 |
| SAGA-07 | `fake_saga_repository.py` save, `state.py:217` | No optimistic concurrency, and `touch()` bumps `version` several times between saves. A protocol-conforming repository would raise `ConcurrencyError` on every second save. `test_durability.py:109` is misnamed | contract suite | **confirmed** | S1 |
| SAGA-08 | `manager.py:340-345` | Every exception per saga is swallowed, including repository and concurrency errors, so the bus sees success and the event is lost | test | **confirmed** | S1 |
| SAGA-09 | `manager.py:142-158`, `saga.py:449-450` | Compensation continues after a failure, although ADR-033 justifies LIFO by dependency. The stack is popped in memory and saved only after every dispatch, so a crash mid-way re-sends every compensation | crash injection | **confirmed** | S1 |
| SAGA-10 | `manager.py:386-388` | Recovering a COMPENSATING saga with an empty stack marks it COMPENSATED without dispatching anything; a non-empty persisted COMPENSATING state looks reachable only in hand-built tests | test plus reachability | **partly** | S3 |
| SAGA-11a | `manager.py:425-431,450` | A pending command that fails hydration is skipped, then deleted by `pending_commands.clear()`, so it is lost silently. `test_recovery.py:312` and `test_durability.py:527` bless this | test | **confirmed** | S1 |
| SAGA-11b | `manager.py:234,442,499` | `retry_count` goes up two or three times per failure | test | **confirmed** | S2 |
| SAGA-11c | `manager.py:276,495` | `max_retries=0` means unlimited in one place and immediate failure in another | test | **confirmed** | S2 |
| SAGA-11d | `manager.py:276-282,402` | Exhausted retries fail without compensating in `_process_saga`, but compensate in `_recover_failed_saga` | decide plus test | **confirmed** | S2 |
| SAGA-11e | `manager.py:433,238-242` | Recovery stamps causation as `state.id`, not the original event id, and dispatches older batches after newer ones | test | **confirmed** | S2 |
| SAGA-12 | `manager.py:535-561` | `on_timeout` forward commands have no `pending_commands` checkpoint, and failures are only logged. If `on_timeout` raises after queuing compensations, they are sent as forward commands and never reach `failed_compensations` | test | **confirmed** | S1 |
| SAGA-13 | `manager.py:258-272,330,359` | Two concurrent first events create two sagas, and `find_by_correlation_id` returns whichever comes first. A terminal saga blocks a new one with the same correlation. Events without a correlation are dropped, and `start_saga` invents a UUID (ADR-032 says `event_id`), so calling it twice is not idempotent | §4.3 | **partly** | S1 |
| SAGA-14 | `registry.py:96`, `manager.py:257` | `_type_map` and `saga_type` use `__name__`, so two `OrderSaga` classes share one state row. Event subclasses are not routed. Calling `bind_to` twice registers the handler twice | §4.5 | **partly** | S2 |
| SAGA-15 | `hydration.py:33` | `importlib` loads a module named by persisted data, a code-loading risk. Unknown keys are stripped, so a renamed field silently takes its default. `__name__` lookup breaks nested classes | test plus decide on an allowlist | **partly** · PD27 | S2 |
| SAGA-16 | `state.py:185,228,276` | Eviction `set(list(s)[-n:])` is arbitrary, not oldest-first, so a recent id is evicted and its event reprocessed. Pruning is triggered from `record_step`, not the manager as the docstring says. `prune_history` has no status guard | property test | **partly** | S1 |
| SAGA-17 | `saga.py:315-352,442` | There is no state machine: `complete`, `fail`, `suspend` and `execute_compensations` accept any source status. `SagaStateError` is never raised | stateful test plus transition table | **confirmed** | S2 |
| SAGA-18 | `saga.py:386-389` | Once `_resume_map` is non-empty, every event type not in it is blocked | decide (ADR-056) plus test | **design** | S3 |
| SAGA-19 | `manager.py` constructor | The manager is annotated with `CommandBus`, though a `MessageBus` works. Wired to a `CommandBus`, the events raised by saga-dispatched commands never reach event handlers | test with both wirings; decide on a dispatcher protocol | **confirmed** · PD27 | S1 |
| SAGA-20 | manager with `MessageBus` wiring | Re-entrant lost update: a saga command whose event re-enters the same saga saves COMPLETED, then the outer handler saves its stale RUNNING copy over it (the wiring `all-modules.md:406` teaches) | test | **confirmed** | S1 |
| SAGA-21 | `recover_pending_sagas` | Aborts the whole batch on the first failing saga, so later sagas stay pending and the documented loop (`saga-error-handling.md:65-72`) skips `process_timeouts` | test | **confirmed** | S2 |

**Stateful test:** a `RuleBasedStateMachine` with these rules: deliver(event), redeliver(same id),
dispatch fails, timeout tick, recover, crash at the Nth save or dispatch. Invariants:
- A terminal state never changes.
- COMPENSATED means every pushed compensation was dispatched exactly once, in LIFO order.
- Nothing is dispatched after FAILED except compensations.
- A duplicate never changes status.
- Every pending command is dispatched at least once.

**Model-based test:** a pure reference model (status, stack, sent commands), compared step by step with
`SagaManager` over the round-trip, version-checking fake repository.

**Transition table:** write the intended saga status table first, with refused cells. It is the oracle for
SAGA-03 and SAGA-17, and probably needs an ADR-034 amendment.

**Test-suite work:**
- Merge `tests/cqrs/saga/` into `tests/saga/`. Test names overlap in `test_manager` (15),
  `test_saga` (12), `test_state` (5) and `test_registry` (1); only `test_fake_saga_repository.py` is
  unique.
- Rewrite the "crash" tests in `test_interruption.py` (e.g. `:333`). They hand-build the post-crash state
  instead of stopping at a real save point.
- Make `test_saga_fail_declarative.py:68,78` assert what reaches the bus.

---

## 8. Module: es

Scope: aggregate, event_stream, event_store, event_sourced_repository, snapshot, upcasting, projection,
checkpoint_store, the ES fakes, and `infrastructure/subscription.py`.

**Contracts:**
- ADR-037: stream value object.
- ADR-038: dual-mode application.
- ADR-039: convention dispatch.
- ADR-040: repository.
- ADR-041: concurrency and idempotency.
- ADR-042: upcaster chain.
- ADR-043: snapshot policy.
- ADR-044: generic fallback.
- ADR-048, ADR-049: subscriptions.
- ADR-052: checkpoint vs snapshot.
- ADR-053: snapshot schema version.

| ID | Where | Scenario | Check | Status | Sev |
|---|---|---|---|---|---|
| ES-01 | `event_sourced_repository.py:95-101` | `pull_events()` drains the aggregate before `append_to_stream`. When the append raises `ConcurrencyError`, the events are lost but `version` stays raised, so retrying `save` on the same instance silently does nothing | fault injector | **gap** | S3 |
| ES-02 | `event_sourced_repository.py:99-113` | The append succeeds, then `snapshot_store.save` raises: the events are stored but the UoW aborts and never publishes them | fault injector | **partly** | S2 |
| ES-03 | `fake_event_store.py` append | The fake records `command_id` before the version check, so a legitimate retry after `ConcurrencyError` raises `DuplicateCommandError` (ADR-041 says atomic) | contract suite | **confirmed** | S2 |
| ES-04 | fake append | Appending `[]` with expected version 0 creates an empty stream. `get_by_id` then returns a version-0 aggregate instead of `None`, and a later expected-0 append succeeds on the "existing" stream | decide plus contract | **confirmed** | S3 |
| ES-05 | fake reads | A negative `from_version`, snapshot version or checkpoint slices from the end of the list | contract suite | **gap** | S3 |
| ES-06 | save vs UoW stamping | Events are appended before the UoW stamps them, so stored events carry `None` correlation and causation | ordering spy | **confirmed** | S2 |
| ES-07 | `aggregate.py:49-57` | A `_when` that calls an `_apply`-based method double-buffers during replay. Replaying a `GenericDomainEvent` hits the documented `raise ValueError` branch, so the aggregate cannot be loaded | test | **partly** | S2 |
| ES-08 | `event_sourced_repository.py:152-154`, `aggregate.py:70` | Snapshot state is `model_dump(mode="python")` restored by raw `setattr`, so nested models come back as dicts. A computed or removed field raises instead of falling back to replay, especially with the default of no policy | round-trip fake plus nested aggregate | **partly** | S1 |
| ES-09 | `fake_snapshot_store.py` | The fake stores snapshots by reference, so `_replay` mutates the stored snapshot and the second load double-applies events (fake-only) | contract suite | **confirmed** | S2 |
| ES-10 | `event_sourced_repository.py:155-165` | Nothing checks `snapshot.version <= stream.version`. A snapshot ahead of its stream, e.g. one kept after a rollback, makes every later save raise `ConcurrencyError` | test | **confirmed** | S2 |
| ES-11 | `event_sourced_repository.py:161-162` | A snapshot without a stream returns an aggregate, so a deleted or erased aggregate is resurrected | decide plus test | **gap** | S2 |
| ES-12 | `snapshot.py:85-87,50-51` | `current_version % threshold` misses snapshots when a batch crosses a multiple (9 → 12). The docstring says "since last snapshot", but the repository passes `len(events)` | property test | **partly** | S3 |
| ES-13 | `exceptions.py` | `StaleSnapshotError` is exported but never raised | decide: use or remove | **design** | S3 |
| ES-14 | `upcasting.py:110` | `register` silently overwrites a duplicate key. It checks neither that `target_version` exists nor that it exceeds `source_version` | test | **confirmed** | S3 |
| ES-15 | `upcasting.py:135-150`, `event_registry.py:165-174` | A version gap stops the chain early and `model_validate` runs on an old payload. The final version is never checked against `event_version`. An upcaster that doesn't bump `event_version` leaves the stale version, so re-serializing causes a double upcast | upcaster graph fuzzing | **confirmed** | S1 |
| ES-16 | ES load path | The fake never deserializes, so upcasting is never exercised on load | round-trip fake | **gap** | S2 |
| ES-17 | `subscription.py:192` | The checkpoint is set to `stream.version`, the global count, not the start plus the events read. A paginating `read_all` skips events | test with a paginating store | **false** | S3 |
| ES-18 | `subscription.py:143-144,173-174` | Offset slicing assumes positions are dense and in commit order. A Postgres sequence has gaps and out-of-order commits, so a late low position is lost (the catch-up gap) | contract plus test | **untestable** | S1 |
| ES-19 | `subscription.py:180-191`, run loop | A poison event retries forever with no dead letter or retry limit, and pins `min_checkpoint` for every subscription | test | **gap** | S2 |
| ES-20 | `subscription.py:180-192` | A partially failed batch leaves earlier events applied, and redelivery double-applies them. Projection writes and the checkpoint are not atomic | crash injection | **design** | S2 |
| ES-21 | runner | Nothing stops two runners, which have no lease, from processing the same events | §4.3 | **gap** | S2 |
| ES-22 | `es/projection.py:81-109` | `_checkpoint` counts applied events, not global position, so it diverges from `CheckpointStore`. `rebuild` does not reset subclass state | test | **partly** | S2 |
| ES-23 | `es/projection.py` | `_when_{ClassName}` collides for same-named classes. A synchronous `_when_*` gives a `TypeError` at `await` | §4.5 | **confirmed** | S3 |
| ES-24 | `unit_of_work.py:150-161` with a shared `EventSourcedRepository` | The repository's event buffer survives a rollback, so the next commit publishes the failed command's events under the new command's IDs. The docs share one repository across units of work (`es-with-cqrs.md:118`, `all-modules.md:426`). Same root as CQRS-K5 | test | **confirmed** · PD22 | S1 |
| ES-25 | `es/event_stream.py`, ADR-037 vs ADR-044 | `EventStream.events` is `Sequence[DomainEvent]` and `GenericDomainEvent` is not a `DomainEvent`, so one unknown legacy type makes `read_stream` and `read_all` raise `ValidationError` and stops every subscription: the crash ADR-044 set out to avoid | test plus §12 A40 | **confirmed** | S1 |

**Property tests:**
- `get_by_id(save(apply*(cmds)))` equals the in-memory state, with and without snapshots at random
  versions.
- A snapshot plus the tail equals a full replay.
- Replay is deterministic.
- After load, `pull_events()` is empty and `version == len(stream)`.

**Concurrency:** two saves to one stream in a task group; exactly one succeeds.

**Contract suites:** EventStore, SnapshotStore and CheckpointStore, run against the fakes and, as an
optional job, a Postgres reference store. A real store is the only way to check ES-18.

**Test-suite work:** the conftest aggregate (`tests/es/conftest.py:33-51`) has flat fields only. Add a
nested-model aggregate for ES-08.

---

## 9. Module: infrastructure

Scope: message_bus, bootstrap, event_registry, message_broker (outbound gateway and relay),
message_subscriber (inbound gateway). `subscription.py` is covered in §8.

**Contracts:**
- ADR-044: registry.
- ADR-045, ADR-058: MessageBus.
- ADR-047: bootstrap.
- ADR-051: broker.
- ADR-059 to ADR-061: subscriber, inbound gateway, tracing.
- ADR-063 to ADR-065: outbound and outbox.

| ID | Where | Scenario | Check | Status | Sev |
|---|---|---|---|---|---|
| INF-01 | `event_bus.py:118-125` vs `message_subscriber.py` dispatch | `EventBus` swallows handler exceptions, so the inbound gateway's NACK path never fires: a failing handler is ACKed and the message lost. `test_inbound_event_gateway.py:254` hides this by monkeypatching `bus.dispatch` | end-to-end test with a real failing handler | **confirmed** | S1 |
| INF-02 | inbound stamping, `behaviors.py:364`, `integration_events.py:47` | Without `correlation_id` nothing is stamped, so dedupe passes through. A payload without `event_id` gets a fresh uuid7 on each redelivery | redelivery test | **confirmed** | S1 |
| INF-03 | `message_subscriber.py` `register_translation` | Registering a translation twice subscribes twice, so a real subscriber processes each message twice; the fake's dict hides it | contract suite | **confirmed** | S2 |
| INF-04 | `message_bus.py` | `GenericDomainEvent` is not a `DomainEvent`, so `MessageBus.dispatch` raises `TypeError` on it. Handlers on a base class never fire (§4.5) | test | **gap** | S3 |
| INF-05 | `bootstrap.py:163` | Inbound gateways start before handlers are registered (ADR-047 order), so messages consumed in that window reach no handler and are ACKed | test | **confirmed** | S1 |
| INF-06 | `bootstrap.py:169` | `broker.start` raising after the gateways started leaks their consumers | fault injector | **confirmed** | S2 |
| INF-07 | `bootstrap.py:108-122` | One `gateway.stop()` raising skips the outbound drain and `broker.stop`. There is no timeout and no idempotency. Shutdown has no tests at all | fault injector | **confirmed** | S2 |
| INF-08 | bootstrap | `event_store` is only logged, and `_event_registry` is stored with no accessor | decide | **confirmed** | S3 |
| INF-09 | `message_broker.py:212` | `mark_published` runs once at the end of a batch. A raise from `mark_failed` or `mark_dead_lettered`, or a cancel mid-batch, republishes every entry already sent in the batch | stateful outbox test | **confirmed** | S2 |
| INF-10 | `message_broker.py:195-201,296,301,306`, `message_subscriber.py:231`, `es/upcasting.py:57` | `f"...{exc}"` from a Pydantic error includes `input_value`. It is persisted through `mark_failed` and `mark_dead_lettered` and logged at ERROR, which leaks content. Saga hydration errors take the same path | §4.6 sentinel | **confirmed** · PD25 | S1 |
| INF-11 | relay lifecycle `:249,260` | `stop()` before `run()` sets `_running` returns while the task keeps running. A dead task leaves `_task` set, so `start()` is a no-op forever. A hung publish blocks `stop` | tests | **confirmed** | S2 |
| INF-12 | relay | `max_attempts <= 0` is accepted | test | **confirmed** | S3 |
| INF-13 | `message_broker.py:288-299` | The entry's `correlation_id` and `causation_id` are not applied at delivery, so they arrive only if the translator stamped them | test | **confirmed** | S2 |
| INF-14 | `event_registry.py:135-181` | A stale `event_version` survives an upcast (see ES-15). Unregistered types are never upcast. A broad `except KeyError` relabels unrelated errors as "unregistered". Name-only identity persists class names: a rename falls back to `GenericDomainEvent` (ADR-044). `register` already raises on a duplicate name (PD11), so test only renames and separate registries | tests | **confirmed** · PD11 | S1 |
| INF-15 | `OutboxEntry.payload`, `GenericDomainEvent.data`, saga command data | Library-owned opaque payload fields appear in `repr()`, so any log of the object carries its content | §4.6 sentinel on `repr()` | **confirmed** · PD23, PD28 | S2 |
| INF-16 | `bootstrap.py:158` | `bootstrap(inbound_gateways=[gw])` without `message_bus=` builds a new bus that is not the gateway's, so handlers registered on the app's bus never run and inbound events are silently lost (the inbound how-to does this) | test; raise when a gateway's bus differs | **confirmed** | S1 |
| INF-17 | `Application` | Has no public `message_bus` or `event_registry`; 69 docs lines in 15 files use them (§12 A26) | add read-only properties or fix docs | **confirmed** | S3 |
| INF-18 | `message_broker.py:146-157` | `OutboundEventGateway` accepts `batch_size <= 0`; with the fake it publishes everything, with SQL `LIMIT 0` nothing | test | **confirmed** | S3 |

**Test-suite work:**
- Add bootstrap and shutdown tests: start order, partial failure, shutdown isolation.
- Make the inbound tests use the shipped `InMemoryMessageSubscriber`, not the private
  `FakeMessageSubscriber` (`test_inbound_event_gateway.py:26`).
- Add caplog sentinel tests (§4.6).

---

## 10. Module: testing fakes

The fakes are both shipped API and the base of the library's own tests. A fake that is more permissive
than its Protocol is itself a defect, because it hides bugs elsewhere (§1). Every row below is closed by
the contract suite for that Protocol (§3). When a fake fails its suite, either fix the fake or document
the divergence in its docstring, if it is deliberate.

| ID | Fake | Divergence from its Protocol | Status | Sev |
|---|---|---|---|---|
| FAKE-01 | `FakeRepository.save` / `get_by_id` | Stores and returns the caller's object. Two loads are the same object, so the version check compares an object with itself and concurrent loads never conflict. The stored state survives a rollback | **confirmed** · PD6 | S1 |
| FAKE-02 | `FakeRepository.save` | Increments `version` only on update, by mutating the caller's object. An insert with `version > 0` is accepted | **gap** | S3 |
| FAKE-03 | `FakeRepository` | `_collected_events` survives a rollback (K5), so a shared repository leaks events into the next commit | **confirmed** · PD22 | S1 |
| FAKE-04 | `FakeUnitOfWork` | Rollback never reverts repository state. `_rolled_back` is never reset. There is no commit-failure injection | **confirmed** · PD6 | S2 |
| FAKE-05 | `FakeOutboxStore` | Has no claim, so two relays fetch the same rows. `append` is visible before commit and survives a rollback. Accepts a duplicate `message_id`. Lets unknown or published ids be marked. `limit=0` returns everything. Orders by insertion, not `occurred_at` | **partly** | S2 |
| FAKE-06 | `FakeProcessedMessageStore` | Commands and events share one key space, which hides CQRS-05. It is not transactional with the handler | **design** | S3 |
| FAKE-07 | `FakeLockProvider` | Any task can release a lock. Releasing an unheld lock raises `RuntimeError`, not the documented `KeyError`. `acquire` never times out | **partly** | S3 |
| FAKE-08 | `InMemoryMessageBroker` | Publishes before `start` and after `stop`, never fails, never serializes | **gap** | S3 |
| FAKE-09 | `InMemoryMessageSubscriber` | An unknown topic returns silently, though the docstring says `KeyError`. One handler per topic hides overwrites. Works when not started. Has no ack/nack, no redelivery and no JSON round trip. Coverage is 45 % | **confirmed** | S2 |
| FAKE-10 | `FakeEventStore`, `FakeSnapshotStore` | ES-03, ES-04, ES-05 and ES-09 | **confirmed** | S2 |
| FAKE-11 | `FakeSagaRepository` | SAGA-07: no version check, no JSON round trip. It does copy on read, unlike `FakeRepository`, so the fakes are inconsistent with each other (PD6) | **confirmed** | S1 |
| FAKE-12 | `FakeSagaRepository.save` (`fake_saga_repository.py:29-30`) | Stores a deep copy before `pull_events()`, so the stored state keeps its pending events and every later save re-emits them with the same `event_id` | **confirmed** | S1 |
| FAKE-13 | `FakeRepository` | A failed command mutates the live stored object and buffers an event; the next command sees the change without a save and publishes the failed command's event (extends FAKE-01, DDD-20) | **confirmed** | S1 |

`tests/testing/` covers only the processed-message store and the UoW. FakeOutboxStore,
FakeLockProvider, the broker and the subscriber get their tests from the contract suites.

---

## 11. The test suite itself

| ID | Problem | Action | Verified |
|---|---|---|---|
| TEST-01 | `tests/saga/` and `tests/cqrs/saga/` duplicate each other (§7) | Merge into one directory and one fixture style | **partly**: 33 shared names, about 31 % of `tests/cqrs/saga`; overlap, not duplication |
| TEST-02 | Tests assert private state (`_committed`, `_rolled_back`, `_behaviors`) | Move to spies and public behaviour | **confirmed**, wider: also `_repos` 12, `_store` 10, `_locks` 6, `_events` 6; some tests write private state |
| TEST-03 | Tests bless suspected bugs: `test_can_change_id`, the `test_exceptions.py` inheritance asserts, `test_recovery.py:312`, `test_durability.py:109` and `:527` | Rewrite each as a strict xfail against the intended contract once triaged | **confirmed**: `tests/cqrs/test_exceptions.py:20,50,86`; `test_durability.py:527` sits in the test at `:500`, sibling `:531` |
| TEST-04 | Shared-instance factories (`lambda: uow`) everywhere | Per-test factories, plus one explicit shared-instance test | **partly**: `lambda: uow` 28 times in `cqrs/test_bus.py` and `infrastructure/test_message_bus.py`; about 70 fresh `FakeUnitOfWork()` without a repository never exercise event collection |
| TEST-05 | No concurrency or cancellation tests outside sagas; asyncio-only `conftest.py`; no leaked-task check | Phase 0 tooling, §4.2 and §4.3 | **partly**: a few concurrency tests exist (`test_locking.py:154,199`, `test_outbound_event_gateway.py:391,409`); `tests/saga` spawns no tasks; no cancellation test anywhere |
| TEST-06 | `test_architecture.py` checks only outward imports from ddd, cqrs and es. Nothing stops `infrastructure → testing`. The README's "ddd uses pydantic and stdlib only" claim is not enforced. The "circular" tests (`:153-170`) duplicate earlier ones, and `:163`, named ddd-es, checks es→cqrs | Add the missing rules, fix the names, add the runtime import checks from §4.7 | **confirmed**; also README:20 is false: `id_generator.py:6` imports `uuid_utils` |
| TEST-07 | `test_package_exports.py`: `pydomain.cqrs.__all__` and `pydomain.infrastructure.__all__` are untested. `EXPECTED_INFRA` lists 4 names, so removing `MessageBus`, `bootstrap`, `InboundEventGateway`, `EventRegistry` or `MessageBroker` would go unnoticed. README import paths are not checked | Exact-match tests for every public `__all__`; a test that imports every path the README shows | **partly**: `EXPECTED_INFRA` has 4 names, 2 of them CQRS; MessageBus, bootstrap and MessageBroker are checked elsewhere; InboundEventGateway, `ddd.__all__` and `saga.__all__` are not |
| TEST-08 | Coverage omits `__init__.py` | Stop omitting it, or cover the import behaviour with §4.7 | **confirmed** (`pyproject.toml:76`) |
| TEST-09 | No property-based or stateful tests anywhere | Phase 0 adds Hypothesis; each module section names its properties | **confirmed** |

---

## 12. ADR conflicts and drift to resolve

The author has to decide each of these before the dependent tests can be written. Accepted ADRs are never
edited, so each outcome is a superseding or amending ADR, or a docs fix.

| # | Conflict | Affects | Verified |
|---|---|---|---|
| A1 | ADR-046 (event handlers fail independently, failures swallowed) vs ADR-060 (the inbound gateway NACKs on dispatch error) and ADR-048 (at-least-once) | INF-01, CQRS-18 | **confirmed**; ADR-058 also says isolation "applies equally" |
| A2 | ADR-058 says domain events have no pipeline behaviors; `register_event` accepts behaviors, and `EventIdempotencyBehavior` exists for them | CQRS-K2, CQRS-05 | **confirmed**; ADR-005, 046 and 058 also cite `MessageBus._dispatch_event(s)`, which no longer exists |
| A3 | ADR-063 types `message_ids` as `Sequence[UUID]`; the code uses `str` | doc drift | **confirmed** (`outbox.py:143,157-163`) |
| A4 | ADR-061 requires both tracing IDs; the inbound code gates on `correlation_id` only. `OutboxEntry.causation_id` is the command id, not the event id | CQRS-14, INF-02, INF-13 | **confirmed**; ADR-061 also contradicts itself (snippet vs Neutral bullet) |
| A5 | ADR-034 says the manager checks `timeout_at` on loaded sagas; the handle path does not | SAGA-06 | **confirmed** |
| A6 | ADR-032 says the first correlation is `event.correlation_id or event.event_id`; `start_saga` invents a UUID | SAGA-13 | **confirmed** |
| A7 | ADR-054 and `pruning.py:7-10` say the manager triggers pruning; `record_step` does | SAGA-16 | **partly**: drift in `pruning.py:7-10` only; ADR-054 agrees with the code |
| A8 | ADR-033 justifies LIFO by step dependency, but compensation continues after a failure | SAGA-09 | **partly**: a conflict only for dispatch failures; the hydration case is accepted by ADR-033 |
| A9 | ADR-047's start order (gateways before handler registration) loses messages | INF-05 | **confirmed** (`bootstrap.py:162`) |
| A10 | ADR-007 says ids are auto-generated; tests named "requires id" | DDD-03, TEST-03 | **partly**: not an ADR conflict; the tests pass through the guard as ADR-007 intends; only their names (`TestNonUuidEntityRequiresExplicitId`) are stale |
| A11 | Copy and serialization semantics of pending events and saga state: no ADR decides them | DDD-01, DDD-02, §4.4 | **confirmed** (DDD-02) |
| A12 | Exact-type vs subclass dispatch, and `__name__` vs qualified-name identity: no ADR decides them | §4.5 | **confirmed** (CQRS-17, INF-04, SAGA-14, ES-23) |
| A13 | ADR-006 says entities must be mutable; ADR-050 says frozen aggregates work. They don't: a frozen aggregate inserts, then fails on update | DDD-01, PD3 | **confirmed** |
| A40 | ADR-037 (`EventStream.events: Sequence[DomainEvent]`) vs ADR-044 (unknown types become `GenericDomainEvent`, which is not a `DomainEvent`): one legacy event stops every read | ES-25 | **confirmed** |

### Docs that teach a defect

These are docs fixes, not decisions, but each one leads a reader into a bug, so they're part of the audit.

| # | Doc | Defect | Affects | Verified |
|---|---|---|---|---|
| A14 | SQLAlchemy repository how-to (`implement-repository.md:133-141`) | Checks a cached row's version, then runs an unconditional `merge`, so concurrent updates are lost. It also stores version 1 on insert, where the fake keeps 0 | DDD-19, PD5 | **confirmed** by reading; SQLAlchemy is not installed to run it |
| A15 | Outbound gateway how-to (`configure-outbound-event-gateway.md:22-28,120-129`) | Builds the outbox writer once, with a session factory. Followed literally, the outbox rows are written outside the command's transaction, which breaks ADR-063's atomicity | PD32 | **confirmed** by reading |
| A16 | `handle-domain-errors.md:108-111` | Catches `ConcurrencyError`, a branch that can never run while CQRS-K4 stands | CQRS-K4, PD19 | **confirmed**: answers 422, never 409, and returns a tuple; the cause is CQRS-13, K4 only picks the 422 |
| A17 | ADR-015, QueryBus docstring | Promise explicit dispatch return types; the buses return a tuple or `Any` | §4.9, PD45 | **confirmed** with mypy: CommandBus returns a tuple, QueryBus and MessageBus `Any` |
| A18 | README, quickstart, value-object how-to, `value_object.py:10-12` | Teach `model_copy(update=…)` on value objects, which skips validation | DDD-13, PD4 | **confirmed**; add `concepts/value-objects.md:60-66`, `recipes/ddd-only-app.md:46` |
| A19 | Value-object concept page | Promises hashable value objects; a `list` field breaks hashing | DDD-13, PD37 | **confirmed**; also `specifications.md:32` |
| A20 | Entity-identity concept page | Lists the UUIDv7 timestamp only as a benefit; ADR-008 accepts it as a leak. Add the caveat | PD1 | **confirmed** |
| A21 | Factory and domain-service pages | Cover child entities only; the domain-service example changes two aggregates without saying how they are saved together | PD14 | **confirmed**, narrowed: whole-aggregate factories are covered; one aggregate creating another is not |
| A22 | `concepts/ddd/specifications.md:70-84` | Asserts `adult.subsumes(senior) is True` with no `subsumes` override; the result is `False` | DDD-15 | **confirmed** |
| A23 | `implement-repository.md:50-58` | The in-memory repository never bumps `version`, so it can never detect a conflict: a third version rule (DDD-19) | DDD-19 | **confirmed** |
| A24 | `handle-es-errors.md` §5 | Says the repository falls back to replay by default; with no policy ADR-053 accepts every snapshot | ES-12 | **confirmed** |
| A25 | `track-checkpoints.md` §4–5 | Saves `projection.checkpoint`, a per-instance count, as the checkpoint; after a restart 4 of 5 events were applied again | ES-22 | **confirmed** |
| A26 | 15 pages under `docs/diataxis` | 69 lines use `app.message_bus` (54) or `app.event_registry` (15), which do not exist | INF-17 | **confirmed** |
| A27 | `configure-inbound-event-gateway.md:91,117,128-136` | Step 4 uses `app` before it exists; step 5 calls `bootstrap` without `message_bus=`, so events are lost | INF-16 | **confirmed** |
| A28 | `use-in-memory-message-subscriber.md:188-189` | Shows `pytest.raises(KeyError)` for an unknown topic; the fake returns silently | FAKE-09 | **confirmed** |
| A29 | `saga/repository.py:53-58`, `saga-repository.md:56`, `arch42/12-glossary.md:527`, `manager.py:456-459` | Say stalled sagas are those with pending commands; the manager also relies on COMPENSATING sagas being returned (`manager.py:479-481`), so an adapter written to the docs never recovers a stalled compensation. The fake is right | SAGA-10 | **confirmed** |
| A30 | `event-versioning.md:60-80` | Upcaster examples never set `event_version`, which produces INF-14's double upcast | INF-14, ES-15 | **confirmed** |
| A31 | `behaviors.py:344-347`, IdempotencyBehavior docstring | The EventIdempotencyBehavior docstring says in-process events have no `causation_id` (false); the IdempotencyBehavior docstring's slot order produces CQRS-04 | CQRS-04, CQRS-05 | **confirmed** |
| A32 | `use-fake-processed-command-store.md:78` | Says `IdempotentCommandIgnored` is raised; nothing raises it | CQRS-13 | **confirmed** |
| A33 | `configure-command-bus.md:163` | Teaches `uow_factory=lambda: uow`, which `register()` and `command-bus.md:64` forbid and which causes CQRS-08 and CQRS-09 | CQRS-08 | **confirmed** |
| A34 | ADR-036 Decision, `state.py` comment | Say eviction keeps the newest ids; it is arbitrary, and the newest was evicted in 70–91 of 200 trials | SAGA-16 | **confirmed** |
| A35 | ADR-056, `on()` docstring | Say `resumes_from=None` means unrestricted; the code blocks it once any step declares `resumes_from` | SAGA-18 | **confirmed** |
| A36 | `snapshot.py:50-51` | Documents `pending_event_count` as "since the last snapshot"; the repository passes the batch size | ES-12 | **confirmed** |
| A37 | ADR-004 | Lists a `QueryExecutionError` that does not exist | — | **confirmed** |

---

## 13. Downstream findings (PD1–PD51)

A downstream application review (Recollections, `docs/tooling/pydomain-findings.md`, 2026-10-20) recorded 51
findings and then judged each one against this library's README and ADRs:
- 18 keep;
- 12 reframe;
- 8 by design;
- 10 project (the application's need, not a library defect);
- 3 wrong.

Every keep and reframe that names a defect is now a row in this plan. By-design and project findings are out
of scope, each with its reason. The wrong ones are closed, so the audit does not reopen them. Some of that
review's evidence citations were wrong (its §3.1); the rows here use corrected references. Features the
review proposed, such as a dispatch context, are not audit work.

| PD | Verdict | In this plan |
|---|---|---|
| PD1 | By design | A20 (docs caveat); no test, ADR-008 accepts the leak |
| PD2 | Keep | DDD-03 |
| PD3 | Reframe | DDD-01, DDD-02, A13 |
| PD4 | Keep | DDD-13, A18 |
| PD5 | Keep | DDD-19, A14, Repository contract suite |
| PD6 | Keep | FAKE-01, FAKE-04, FAKE-11 |
| PD7 | Keep | CQRS-23 |
| PD8 | Project | Out: idempotent delete is deliberate (`repository.py:69-74`) |
| PD9 | By design | Out: no database adapters in the core (README) |
| PD10 | Wrong | Closed: the event registry already upcasts at Level 3 (`event_registry.py:166-174`). ES-15 and INF-14 test that path |
| PD11 | Reframe | INF-14 |
| PD12 | By design | Out: ADR-021 rejects context variables. CQRS-19 covers queries only |
| PD13 | Wrong (framing) | DDD-17, §4.7: the cause is `pydomain/__init__.py`, not `pydomain.testing` |
| PD14 | Keep (docs) | A21 |
| PD15 | Project | Out: retry belongs to the caller (ADR-041). CQRS-13 covers the wrapped error |
| PD16 | By design | Out: ADR-023 |
| PD17 | Keep | CQRS-K1, CQRS-04 |
| PD18 | Keep | CQRS-K3, ordering tests (§6) |
| PD19 | Keep | CQRS-K4, CQRS-13, A16 |
| PD20 | Project | Out: error codes are an application concern |
| PD21 | Reframe | DDD-20 |
| PD22 | Keep | CQRS-K5, CQRS-06, CQRS-24, FAKE-03 |
| PD23 | Reframe | INF-15, §4.6 |
| PD24 | Reframe | INF-10, only where the library itself validates stored or inbound payloads |
| PD25 | Keep (narrowed) | INF-10 |
| PD26 | Project (+ small keep) | CQRS-25 |
| PD27 | By design (+ keep one) | SAGA-15, SAGA-19 |
| PD28 | Wrong (mostly) | Closed: the default formatter logs no payload. The repr part is INF-15 |
| PD29 | Project | Out: audit is an application behavior |
| PD30 | Reframe | Docs; the content sentinel (§3) is the library's own canary |
| PD31 | By design / reframe | Out: an optional dispatch context is a feature, not a defect |
| PD32 | Keep (docs) | A15 |
| PD33 | By design | Out: ADR-046. The related conflict is A1 |
| PD34 | Reframe | Out: an outbox partition key is a feature |
| PD35 | Project | Out: retention and erasure are application policy (ADR-065) |
| PD36 | Reframe | DDD-07 |
| PD37 | Reframe | DDD-13, A19 |
| PD38 | Project | Out |
| PD39 | Project | Out: ADR-012 |
| PD40 | Reframe | DDD-11 |
| PD41 | Keep | DDD-21 |
| PD42 | By design | Out: ADR-006 |
| PD43 | Project | Out |
| PD44 | Keep | §4.9 |
| PD45 | Keep | §4.9, A17 |
| PD46 | Project | Out: ADR-014 |
| PD47 | Reframe | §4.9, the typing part only; a `@bus.handles` decorator contradicts ADR-047 |
| PD48 | Keep | Phase 0 CI matrix (§3) |
| PD49 | Reframe | Phase 0 contract suites (§3) |
| PD50 | Keep | CQRS-K2, CQRS-05 |
| PD51 | Keep | DDD-09 |

---

## 14. Order of work, agents and timeboxes

The phases are sequential. Inside phases 1 and 2 the modules are independent and can run as parallel
agents, because they write to disjoint test directories.

| Phase | Work | Who | Timebox |
|---|---|---|---|
| **0. Tooling** | §3: dev dependencies, fault injector, ordering spy, round-trip and version-checking fakes, leaked-task fixture, import probe, content sentinel. Contract suites for the 12 Protocols, run against the shipped fakes; the expected red results are the FAKE rows | One agent, sequential (everything after depends on it) | 2 days |
| **1. Confirmed S1 defects** | Strict-xfail tests for every **confirmed** or **partly** row marked S1 (listed in the findings report §2); each probe in the findings report is the starting point | Five parallel agents, one per module directory | 2 days |
| **2. Remaining confirmed rows** | All other **confirmed** and **partly** rows, the cross-cutting checks §4.1–§4.9, the stateful and property tests. **gap** rows wait for phase 4; **design** and **false** rows get no test unless they guard a real risk | Five parallel agents, one per module | 3 days |
| **3. Review the tests** | The defects are already verified (§1.1). An independent agent now checks each red test: does it assert the contract rather than an implementation detail, and does it fail for the verified reason? | One verifier per module; never the agent that wrote the test | 1 day |
| **4. Triage** | The author decides §12 and the 18 **gap** rows, confirms the corrected severities, and adds each test path and fix issue to the findings report | Author, with one agent drafting | 1 session |
| **5. Fix** | One DCE issue per fix, or per small cluster in the same function. Branches `feature/DCE-NN-*` from `dev`, commits `DCE-NN: fix(<scope>): …`. S1 first, then by release: non-breaking fixes in 0.3.x; breaking ones with an ADR in 0.4 or 0.5 | Normal workflow | per issue |

Module order inside each phase, when there aren't enough agents to run all five in parallel:
1. **cqrs core and the fakes.** Everything else runs on them; Level 3 users depend on them first.
2. **infrastructure.**
3. **cqrs.saga.**
4. **es.**
5. **ddd.**

Rules for the agents:
- Each agent writes only in its module's test directory, plus `tests/support/` if phase 0 allows.
- No source changes before phase 5. Phases 1–3 produce tests and notes only.
- The agent reports every red test with its AUD ID, the contract it asserts (ADR, doc or docstring),
  and the observed behaviour.
- `make test`, `make type` and `make lint` stay green, with red tests as strict xfail.

---

## 15. Exit criteria

The audit is done when all of these hold:
- Every row in §5–§11 has a test, or a written reason for having none, and a class from §2.3.
- Every §12 conflict is decided, and recorded as an ADR or a docs change; every docs defect (A14–A37) is fixed
  or has an issue.
- Every PD finding that §13 maps to a row is covered by that row's test or docs fix.
- All 12 contract suites exist in `pydomain.testing.contracts` and pass against every shipped fake, or
  the fake documents its divergence.
- The ordering tests (§6) and import-isolation tests (§4.7) exist; they may be strict xfail until fixed.
- A `mutmut` baseline is recorded for the modules in §4.8, and every surviving mutant in
  `unit_of_work.py`, `command_bus.py`, `behaviors.py`, `saga/saga.py` and
  `event_sourced_repository.py` has been reviewed.
- `docs/others/bug-audit-findings.md` lists every AUD ID with its class, severity, test and fix issue.
- Every S1 finding has a DCE issue.
