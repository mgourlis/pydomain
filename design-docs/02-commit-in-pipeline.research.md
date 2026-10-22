# Research: Commit Inside the Command Pipeline

- **Date:** 2026-10-22
- **Issue:** DCE-142 (Feature DCE-130, fix plan cluster W2-1)
- **Scope:** How `CommandBus` runs the pipeline, the unit of work's commit and rollback, and the built-in
  behaviors today; what the Protocols, fakes, contract suites, ADRs and docs promise about their order.
- **Method:** Source reading at `origin/dev` (f9779a7), the audit probes in `tests/support/probes/`, and three
  scratch scripts run against this tree (not committed; their output is quoted below). Facts only: options
  and opinions are in [02-commit-in-pipeline.alignment.md](02-commit-in-pipeline.alignment.md).
- **Paths:** source paths are relative to `src/pydomain/`; `l.N` is a line number.

The rows in scope are CQRS-K1, CQRS-K3, CQRS-04, CQRS-07, CQRS-21, CQRS-11 and the rest of FAKE-04 (no
commit-failure injection in `FakeUnitOfWork`). Their audit entries: `docs/others/bug-audit-findings.md` l.81,
83, 85, 160, 167, 199, 213; `docs/others/bug-audit-plan.md` l.348, 350, 361, 364, 368, 378, 591.

---

## 1. How `CommandBus` builds the pipeline, commits and rolls back

### Files

- `cqrs/command_bus.py`: `CommandBus.register` (l.54-88), `CommandBus.dispatch` (l.90-201).
- `cqrs/behaviors.py`: `MessageContext` (l.34-49), `MessagePipeline` (l.73-135).

### Findings

**F1.1 One pipeline per command type, built at registration.** `register` stores a
`_HandlerEntry(pipeline=MessagePipeline(handler=handler, behaviors=behaviors), uow_factory=...)` (l.85-88).
`MessagePipeline` is shared by all three buses: `cqrs/query_bus.py` l.55 and `cqrs/event_bus.py` l.71 build
it too.

**F1.2 The terminal step is the handler only.** `MessagePipeline.execute` sets `ctx.handler = self._handler`
(l.111) and builds `terminal()`, which awaits `self._handler(message, ctx.uow)` for `MessageKind.COMMAND` and
`self._handler(message)` otherwise (l.113-116). Behaviors are wrapped in `reversed()` order, so the first in
the list is the outermost (l.118-122). `LoggingBehavior` names the handler from `ctx.handler.__name__`
(l.161-165); `EventBus` does the same in its failure log (`cqrs/event_bus.py` l.123).

**F1.3 The bus commits after the whole pipeline returns.** Inside `async with uow:` the bus awaits
`entry.pipeline.execute(ctx, command)` and then `uow.commit()` (`command_bus.py` l.170-173). Every behavior's
"after `next()`" code has therefore finished before the commit starts.

**F1.4 The bus never calls `rollback()`.** A failure in the pipeline or the commit is recorded in `failure`
and re-raised (l.174-176); `uow.__aexit__` rolls back a scope that did not commit (l.163-167 comment;
`unit_of_work.py` l.159-162). The failure is wrapped in `CommandExecutionError` outside the `async with`
block (l.178-194), so a rollback that also raises becomes its `__context__` plus a type-only note. A
cancellation (a `BaseException` that is not an `Exception`) is re-raised unwrapped with the note
(l.185-190, ADR-078). A `__aexit__` that suppresses the failure still gets it raised (l.195-198).

**F1.5 The UoW scope spans the whole pipeline.** `async with uow:` (l.170) is entered before the first
behavior runs, so validation, idempotency lookups and lock waits all run inside the scope.
`AbstractUnitOfWork.__aenter__` only resets the scope (`unit_of_work.py` l.137-139); whether a storage
transaction is open during that time is up to the adapter.

**F1.6 `ctx.new_events` is set after everything.** `ctx.new_events = raw_events` is the second-last line of
`dispatch` (l.200), after the `async with` block and its error handling. No behavior can see it (CQRS-11).
`raw_events = uow.collect_events()` is read inside the block, after the commit (l.177).

**F1.7 Tracing ids are set on the UoW by `setattr`** (l.151-152), because the `UnitOfWork` Protocol does not
declare them (W2-10, CQRS-12, owns this).

### Scratch evidence: the order today

`order_today.py`: documented slots (logging, validation, idempotency, locking) plus a custom behavior
registered outermost, spy hooks on every step.

```
 1. uow.__aenter__            7. handler                    13. pull_events   (_collect_and_stamp)
 2. custom.before             8. lock.release               14. outbox.write(1)
 3. logging.before            9. store.set                  15. _commit
 4. validate                 10. logging.after              16. uow.__aexit__(committed=True)
 5. store.get -> MISSING     11. custom.after(new_events=0, 17. pull_events   (committed-exit drain)
 6. lock.acquire                 committed=False)
12. _flush
```

The locks are released (8) and the result stored (9) before `_flush` (12). The outermost behavior sees
`new_events=0` and an uncommitted UoW after `next()` returns (11).

---

## 2. Every await in `commit()` and `rollback()`

### Files

- `cqrs/unit_of_work.py`: `UnitOfWork` Protocol (l.23-81), `AbstractUnitOfWork` (l.84-373).

### Findings

**F2.1 `commit()`** (l.168-211) has three awaits, plus whatever the adapter awaits inside them:

| Step | Line | Await |
|---|---|---|
| guard: `_committed` → `UnitOfWorkAlreadyCommittedError` "first commit persisted"; `_commit_started` → the same error "first commit failed" | l.194-205 | none |
| `_commit_started = True` | l.206 | none |
| `_flush()` | l.207 | yes |
| `_collect_and_stamp()`: drains every repository with `pull_events()` and stamps each event | l.208, 358-373 | none (sync) |
| `_write_outbox()` → `self._outbox_writer.write(self.collect_events())` when a writer was given | l.209, 339-341 | yes |
| `_commit()` | l.210 | yes |
| `_committed = True` | l.211 | none |

A cancellation or error at any of the three awaits leaves `_committed = False` and `_commit_started = True`.
The events stamped at l.208 are already in `self._events`.

**F2.2 `rollback()`** (l.213-255) awaits once: `_rollback()` (l.238). The drain
`_discard_staged_events()` (l.257-271) is synchronous and runs in a `finally`-like path whether `_rollback`
raised or not; `_start_scope()` (l.127-131) then clears `_events`, `_committed` and `_commit_started`. When
`_rollback` raises any `BaseException`, a drain error becomes a type-only note on it (l.239-251, B-88).

**F2.3 `__aexit__`** (l.141-162): a committed scope only drains (`_discard_staged_events`, sync); any other
scope awaits `self.rollback()`. Nothing is shielded: the only `shield` in the CQRS layer is none (the word
appears in ADR-076 only as two rejected alternatives, l.137 and l.139).

**F2.4 A scope commits once** (Protocol l.35-39, l.55-65; ADR-068 l.83-94). After a commit that failed
part-way, a second `commit()` raises until `rollback()` or a new `async with` starts a new scope (l.180-185).

### Scratch evidence: failure and cancellation at each await

`scenarios.py`, documented order (idempotency, locking) with a spy store and a spy lock provider; the
commit can raise or block (then the task is cancelled) at each await. Then the same `command_id` is
dispatched again with no fault.

```
raise  at _flush            : caller=CommandExecutionError db_row=False cached=Res log=[lock.acquire, handler, lock.release, store.set, _flush, _rollback]
         retry same command_id: handler ran=False, events=0, db_row=False
raise  at outbox.write      : caller=CommandExecutionError db_row=False cached=Res ... _flush, outbox.write, _rollback]
raise  at _commit.before-db : caller=CommandExecutionError db_row=False cached=Res ... _commit.before-db, _rollback]
raise  at _commit.after-db  : caller=CommandExecutionError db_row=True  cached=Res ... db-committed, _commit.after-db, _rollback]
cancel at _flush            : caller=CancelledError        db_row=False cached=Res ... _flush, _rollback]
cancel at outbox.write      : caller=CancelledError        db_row=False cached=Res ... outbox.write, _rollback]
cancel at _commit.before-db : caller=CancelledError        db_row=False cached=Res ... _commit.before-db, _rollback]
cancel at _commit.after-db  : caller=CancelledError        db_row=True  cached=Res ... db-committed, _commit.after-db, _rollback]
         (every retry: handler ran=False, events=0)
cancel during the rollback  : caller=CancelledError log=[handler-raises, _rollback]   (_rollback never returned)
```

- CQRS-K1 holds at every await: the result is cached before the commit, and every retry returns it without
  running the handler, also when nothing was persisted.
- CQRS-07: a cancellation (or error) after the storage commit took effect still rolls back the scope and
  clears its events, and the caller sees a failure although the row exists. A second cancellation during
  the rollback interrupts `_rollback` (the audit probe `cqrs/p_04_09.py` prints the same:
  `second cancel during awaited rollback: log=['rollback-start']`).
- The probe `cqrs/p_known.py`: `K1 retry returned PlaceOrderResult, handler calls=1, events=0, order in
  db=False`.

---

## 3. What each built-in behavior does before and after `next()`

All in `cqrs/behaviors.py`.

| Behavior | Before `next()` | After `next()` returns | After `next()` raises |
|---|---|---|---|
| `LoggingBehavior` (l.138-207) | logs "Processing ..." (l.175-184) | logs "Completed ... in Xms" (l.199-207) | catches `Exception` only, logs "Failed ...: <type>" and re-raises (l.189-198); a cancellation is not logged |
| `ValidationBehavior` (l.210-245) | runs every validator for `type(ctx.message)`, awaiting awaitables (l.239-244) | nothing | nothing |
| `IdempotencyBehavior` (l.464-499) | no `command_id` in metadata → pass through (l.489-491); `store.get(command_id)`; not `MISSING` → return cached, skip `next()` (l.493-495) | `store.set(command_id, result)` (l.498) | nothing: no record, nothing to undo |
| `AggregateLockingBehavior` (l.321-458) | resolve, sort, dedupe keys (l.385); empty → pass through (l.386-387); `acquire` each in order, recording it (l.391-394) | release in reverse, inline; a failed release is a WARNING and the result is still returned (l.402-408) | release the acquired keys, note release failures on the error, re-raise (l.396-400); cancellation hand-over to a background task (l.410-458, ADR-076) |
| `EventIdempotencyBehavior` (l.502-560) | not a `DomainEvent` or no `causation_id` → pass through (l.547-548); `check_and_set(causation_id)`; true → skip (l.550-558) | nothing | nothing (the mark stays: CQRS-K2, W2-2) |

**F3.1 The order is a docstring promise, not enforced.** `IdempotencyBehavior` says "Pipeline slot 3: after
`ValidationBehavior` (slot 2) and before `AggregateLockingBehavior` (slot 4), avoiding wasted lock work on
already-processed commands" (l.472-474) and carries the P-25 known-limit caveat (l.476-478). Nothing checks
the list's order at registration.

**F3.2 `IdempotencyBehavior` never calls `check_and_set` or `contains`.** It uses `get` and `set` only
(l.493, l.498). The audit plan row CQRS-04 (l.361) says so: "`check_and_set` exists but is unused here".

**F3.3 `LoggingBehavior` reports a commit failure as success today.** Its "Completed" line is written before
the commit starts (scratch, §8).

### Scratch evidence: concurrent duplicates (CQRS-04)

Two `gather`ed dispatches of one command, a handler that yields once:

```
order idem,lock: handler runs=2  log=[lock.acquire, lock.acquire, handler, lock.release, store.set, ..., handler, ...]
order lock,idem: handler runs=1  log=[lock.acquire, lock.acquire, handler, store.set, lock.release, ..., lock.release, ...]
```

With the lock outermost, the second dispatch waits on the lock, then finds the cached result. It still
enters its own UoW scope and commits an empty one. The audit probe prints the same counts (`cqrs/p_04_09.py`:
`documented(idem,lock) ... handler runs=2`; `lock-outermost(lock,idem) ... handler runs=1`). Without the
yield in the handler both orders run it once: the race needs an await between `get` and `set`.

---

## 4. `ProcessedMessageStore` and `LockProvider`: Protocols, fakes, contract suites

### `ProcessedMessageStore` (`cqrs/idempotency.py` l.17-57)

- Four methods: `get(message_id) -> Any` (`MISSING` if absent), `set(message_id, result)`,
  `contains(message_id) -> bool`, `check_and_set(message_id) -> bool` (true if already processed). No method
  deletes or releases an id, and none has a TTL or a state other than present/absent.
- `@runtime_checkable`; the docstring asks implementations for "atomic check-and-set semantics to prevent
  races when the same message arrives concurrently" (l.25-26).
- ADR-018 (l.14, l.32-50) defines the three-state `get`; ADR-062 (l.63-65) added `check_and_set` for events.
- **Key spaces are not specified.** The fake keeps one dict for both: `check_and_set` stores `True` under the
  id (`testing/fake_processed_message_store.py` l.34-43). The how-to's Redis example keeps two: results under
  `cmd:<id>`, marks under `seen:<id>` (`docs/diataxis/how-to/cqrs/add-idempotency.md` l.145-160).
  Scratch: after `check_and_set(id)` on the fake, `get(id)` returns `True` and `contains(id)` is true. On the
  Redis example, `get` would return `MISSING`.
- Contract suite `testing/contracts/processed_message_store.py`: 10 cases (l.31-131): protocol, `get`
  missing and cached, cached `None` is not `MISSING`, `contains` before and after `set`, `check_and_set`
  first/second/marks, and "concurrent `check_and_set` admits one caller" (l.117-131). No case relates
  `check_and_set` to `get` or `set`.

### `LockProvider` (`cqrs/locking.py` l.14-49)

- `acquire(key)` and `release(key)`. A cancelled `acquire` never holds the key; locks are not reentrant;
  `release` must be ownership-safe, tied to the acquire by a per-acquire token in a `ContextVar` (l.21-48,
  ADR-076).
- `FakeLockProvider` (`testing/fake_lock_provider.py` l.34-127): one `asyncio.Lock` per key; raises
  `LockReentryError` when the holding task acquires again (l.91-93); a release from a context without a token
  raises `RuntimeError`, a stale token does nothing (l.116-127).
- Contract suite `testing/contracts/lock_provider.py`: 8 cases (l.35-204), including cancelled acquire,
  same-task reentry and the late release.
- No timeout or lease: a lock is held for as long as the behavior holds it. Holding it longer (through a
  commit) changes nothing in the Protocol.

### `FakeUnitOfWork` (`testing/fake_unit_of_work.py` l.10-73)

- `_flush`, `_commit` are no-ops; `rollback()` records `rolled_back` and drains (l.62-73); `rolled_back` is
  reset on each `async with` (l.53-56, B-94).
- **No commit-failure injection** (FAKE-04 rest): no parameter or attribute makes `_flush`, `_write_outbox`
  or `_commit` raise or block. The probe `fakes/probe_repo_uow.py` prints `commit-failure hook attrs: []`.
  Tests that need a failing commit subclass `AbstractUnitOfWork` themselves (for example the probes'
  `SpyUoW` in `tests/support/probes/cqrs/common.py`).
- The rollback does not undo `FakeRepository` saves (`aggregate still stored after rollback: True`); that
  part of FAKE-04 belongs to W2-6 (decision log P-31).

### `UnitOfWorkContract` (`testing/contracts/unit_of_work.py` from l.19)

Cases on commit, rollback, exit, the drains and a second commit (l.60-206). None injects a failure inside
`commit()`, and none cancels.

---

## 5. Calling `next()` twice

`NextHandler` (`behaviors.py` l.52-54) is a plain callable; nothing counts its calls. Each call runs the
inner behaviors and the handler again, with the same `ctx` and the same UoW.

Scratch (`scenarios.py`), today:

```
retry after handler failure: handler runs=2, commits=1, events committed=2
next() twice, both succeed:   handler runs=2, commits=1, events committed=2
retry outside locking: log=[lock.acquire, lock.release, retry, lock.acquire, lock.release, _flush, ...], events=2
```

The first run's events stay in the repository buffer and are committed with the second run's (CQRS-21).
The audit probe `cqrs/p_15_25.py` `c21` shows the same (`handler calls=2, events committed=2`).

No ADR, docstring or Diataxis page says whether a behavior may call `next()` more than once (survey of
ADR-017 to 020, 062, 068, 076, 078 and the six pages of §9.2).

---

## 6. Nested dispatch

- A handler that dispatches another command calls `CommandBus.dispatch` (or `MessageBus.dispatch`,
  `infrastructure/message_bus.py` l.162-201) again. The nested dispatch gets a fresh UoW from its own factory
  and its own pipeline; its commit finishes before the outer handler returns.
- `MessageBus.dispatch` dispatches the returned events through `EventBus.dispatch_many` after the command
  bus returns (l.197-199), so event handlers run after the outer command's locks are released, today and
  under any order inside the pipeline.
- Locks are not reentrant: the nested command's keys must not include one the outer holds
  (`behaviors.py` l.362-365).

Scratch (`scenarios.py`), outer command locks `order:A`, its handler dispatches `Inner` on `order:A`, then
on another key:

```
[lock.acquire, outer-handler, lock.acquire, _rollback, inner raised CommandExecutionError(LockReentryError),
 lock.acquire, inner-handler, lock.release, _flush, outbox.write, commit:inner,
 lock.release, _flush, outbox.write, commit:outer]
```

The inner command commits (and releases) before the outer one; the outer lock is released before the outer
commit. A shared UoW factory (`lambda: uow`) makes the inner dispatch reuse the outer UoW (CQRS-08, fixed in
part by ADR-068; the docs stopped teaching it).

---

## 7. Cancellation at each await, and backends

- Cancellation points in one dispatch: `__aenter__` (adapter), each async validator, `store.get`,
  each `acquire`, the handler, each `release`, `store.set`, `_flush`, `_write_outbox`, `_commit`, and on
  failure `_rollback`.
- Lock release under cancellation is handled (ADR-076): inline releases, hand-over of the rest to a
  background task when the task is cancelled during them. This needs asyncio; with no asyncio task (trio
  through anyio) the releases run inline (`behaviors.py` l.293-303, B-69).
- **anyio is not a runtime dependency.** `pyproject.toml` lists `pydantic` and `uuid-utils` only;
  `anyio` is imported only by the contract suites (`testing/contracts/processed_message_store.py` l.7,
  `testing/contracts/lock_provider.py` l.7). ADR-076 l.137-139 rejected `asyncio.shield` for releases
  because `release()` would not run in the acquiring task; ADR-076 l.138 notes that an anyio cancel scope
  cancels every later await.
- Today a cancellation during the commit reaches `__aexit__`, which awaits an unshielded rollback (§2).

---

## 8. A scratch prototype: the commit as the terminal step

`proto_terminal.py` (scratch only, not a proposal): a `CommandBus` subclass that wraps the handler so
`uow.commit()` runs right after it, inside every behavior; `async with uow` and `__aexit__` stay in the bus.

```
success:              [lock.acquire, handler, _flush, outbox.write, _commit..., lock.release, store.set]
fail at _commit:      [lock.acquire, handler, _flush, outbox.write, _commit.before-db, lock.release, _rollback]
    retry same command_id: handler ran=True, events=1
K3 counter, two locked read-modify-write increments, slow _commit:  CommandBus: counter=1   prototype: counter=2
CQRS-04, documented order idem,lock, prototype:                     handler runs=2
Twice  (next() twice, no fault):  CommandExecutionError from UnitOfWorkAlreadyCommittedError;
                                  log=[handler, _flush, ..., _commit.after-db, handler]   (first run is persisted)
RetryOnce (commit fails once):    CommandExecutionError from UnitOfWorkAlreadyCommittedError;
                                  log=[handler, ..., _commit.before-db, retry after RuntimeError, handler, _rollback]
LoggingBehavior, commit fails:    CommandBus: [Processing, Completed]     prototype: [Processing, Failed ...: RuntimeError]
```

Observations from the prototype:

1. K1 and K3 go away with the order alone: the store is written after the commit, and the second increment
   waits for the first commit.
2. CQRS-04 does not: with idempotency outside locking, both duplicates pass `get` before either commits.
3. The rollback still runs after the locks are released, because `__aexit__` stays in the bus.
4. A second `next()` after a successful commit makes the dispatch fail with
   `UnitOfWorkAlreadyCommittedError` although the first run is persisted; a retry after a failed commit
   fails the same way, because the scope is spent (§2, F2.4).
5. Every behavior's "after `next()` raises" path now also sees commit failures (logging says "Failed").
6. The handler wrapper must keep the handler's `__name__`, or the log names the wrapper (F1.2).

---

## 9. What the ADRs promise that a change would touch

From a survey of ADR-017 to 020, 062, 068, 076 and 078 (quotes shortened).

| ADR | Status | Promise |
|---|---|---|
| ADR-017 | Accepted, retroactive | Order "logging → validation → idempotency → locking → handler" (l.15); slot table 1-4 (l.51-56); "Behavior order matters and must be documented" (l.88) |
| ADR-018 | Accepted, retroactive | Three-state `get` with `MISSING` (l.14, l.32-50); nothing on when `set` runs |
| ADR-019 | Accepted, retroactive; amended by ADR-076 | Sorted acquire, reverse release around `next()` (l.28-49, l.51, l.55) |
| ADR-020 | Accepted, retroactive; amended by ADR-068 | "The `CommandBus` (not the handler) creates, commits, and rolls back" (l.122); code shows `pipeline.execute` then `uow.commit()  # Bus commits` (l.131-132); stamping "after handler completion but before commit" (l.118); `__aexit__` rolls back (l.142, l.183) |
| ADR-062 | Accepted, 2026-06-02 | `check_and_set` is "atomic check-and-mark ... where no result caching is needed (events)" (l.63-65); sibling events "never incorrectly skipped" (l.114) |
| ADR-068 | Accepted, 2026-10-21 | Bus never calls `rollback()`; `__aexit__` rolls back once (l.61, l.67-69); a second commit raises (l.83-94); cancellation not wrapped (l.79-82, l.106-107); scope-keyed buffers "pair with W2-1" (l.141); locks "release them before the commit and the exit (CQRS-K3, fixed by W2-1)" (l.171-173) |
| ADR-076 | Accepted, 2026-10-21 | Release after the handler returns or after any `BaseException` (l.43-46); release failure after success: "the command still succeeds ... so the bus commits" (l.74-76); `asyncio.shield` rejected twice (l.137, l.139); "holding them through the commit is W2-1" (l.189) |
| ADR-078 | Accepted, 2026-10-22 | The bus re-raises the original `BaseException`, a cancellation unwrapped, with type-only notes (l.43-51); deferring the UoW cases to W2-1 was rejected (l.98) |

The decision log adds: P-25 (keep the slot order and the known-limit caveat until W2-1; "the caveat does
not describe the fix's design"), B-19 and F-6 (scope-keyed buffers deferred; "pairs with W2-1"), B-88 (the
cancellation and drain notes, done in ADR-078), P-31 (CQRS-11 and FAKE-04's commit-failure injection
belong to W2-1).

---

## 10. What the docs teach

From the same survey (Diataxis pages of fix plan §9.2, arc42 sections of §9.2).

| Page | Teaches |
|---|---|
| `concepts/cqrs/pipeline-behaviors.md` | Slot table 1-5 plus the terminal handler, "event behaviors are a second list" (l.186-195); tracing "slot 0" (l.199); `MessageContext` code lists `new_events` without explaining it (l.54); custom `TracingBehavior` and `MetricsBehavior` around `await next()` (l.150-181); the CQRS-04 caveat (l.197) |
| `concepts/cqrs/idempotency-and-locking.md` | Idempotency before locking "so duplicate commands skip lock acquisition" (l.15, l.163-173); flow `get` → handler → `set`, no commit (l.56-59); locks "released before the commit and the unit of work's exit (a known limit ...)" (l.72); release failure "still succeeds and commits" (l.128-133) |
| `concepts/cqrs/command-bus.md` | Steps "4. Run pipeline" then "5. On success: commit UoW" (l.18-20, l.65-67); the handler never commits (l.69); cancellation (l.71) |
| `how-to/cqrs/add-idempotency.md` | Slots 1-4 (l.93-101); the known limits: concurrent duplicates, and "stores the result when the handler returns, and the bus commits after the pipeline ... a retry ... gets the stored result back" (l.103); flow `get` → handler → `set` (l.108-117); Redis store with separate `cmd:` and `seen:` keys (l.145-160) |
| `how-to/cqrs/add-pipeline-behavior.md` | "do something before `await next()`, then do something after" (l.142); `MetricsBehavior` counting failures from `next()` (l.125-139); slot table with `EventIdempotencyBehavior` as slot 5 (l.211-217) |
| `how-to/cqrs/add-distributed-locking.md` | Slots 3 and 4 (l.146-147); release however the command ends (l.171-173); "The command commits" after a failed release (l.174); nothing on commit timing |
| arc42 §4.5 S4 (`04-solution-strategy.md` l.144-195) | Diagram idempotency → locking → handler; "stores result after success" (l.173); `new_events` "collected during execution" (l.194) |
| arc42 §5.3.5 (`05-building-block-view.md` l.363-389) | Behaviors run before `next()` and after it returns (l.371); `new_events` field (l.379) |
| arc42 §6.1 (`06-runtime-view.md` l.7-152) | Sequence: 8. `pipeline.execute`, 8a behavior chain, then 9. `uow.commit()` by the bus with 9a-9d, 9e exit (l.13-83); no lock release step; cancellation row (l.147) |
| arc42 §8.5 (`08-concepts.md` l.217-261) | Nested diagram and slot table 1-4, idempotency mechanism "`get/set` by `command_id`" (l.255-260) |
| arc42 §10.6 (`10-quality.md` l.339-387) | Events after commit (l.356); "Duplicate dispatches are detected and return the cached result" with no known limit (l.376) |
| arc42 §11 (`11-risks.md` l.254, l.264) | Locks released before the commit and the exit (CQRS-K3, W2-1); F-6 "pairs with W2-1" |

Nothing in the docs or the ADRs covers a behavior calling `next()` twice. `ctx.new_events` is assigned only
at `command_bus.py` l.200. No documented behavior uses `ctx.uow`: outside `docs/others`, the name appears
only in the terminal step (`behaviors.py` l.104-115) and ADR-017's copy of it (l.39).

---

## 11. Code and docs other Wave 2 clusters share

- **W2-2 (DCE-131, event idempotency)** changes `EventIdempotencyBehavior` and the event-side use of
  `ProcessedMessageStore` (`check_and_set`, CQRS-K2, CQRS-05); its plan row asks for "separate key spaces"
  (`docs/others/bug-fix-plan.md` l.466). The store Protocol, the fake and the contract suite of §4 are shared
  with W2-1. The event pipeline has no UoW and no commit (`cqrs/event_bus.py`).
- **W2-10 (tracing)** owns the `setattr` tracing channel (F1.7) and the tracing behavior taught at slot 0
  (`pipeline-behaviors.md` l.199). Where the commit runs decides whether a span around `next()` includes it.
- **W2-6** owns the `FakeRepository` part of FAKE-04 (rollback keeps the saves).
- **F-6** (scope-keyed buffers) would change how repositories buffer events; today both the commit's
  `_collect_and_stamp` and the exit drain pull from shared buffers by repository, not by scope.

## Unresolved

- What a real adapter's `__aenter__` does (begin a transaction or not) is outside the library; whether lock
  waits and validation run inside an open storage transaction depends on it (F1.5).
- Whether a storage driver treats a rollback after a commit that took effect as a no-op is driver-specific
  (audit row CQRS-07, "partly").
