# Alignment: Commit Inside the Command Pipeline

- **Date:** 2026-10-22
- **Issue:** DCE-142 (Feature DCE-130, fix plan cluster W2-1)
- **Brief:** the W2-1 row of `docs/others/bug-fix-plan.md` §9.1 and §9.2. It is the plan's proposal: "The
  commit runs in the pipeline's terminal step, still owned by `CommandBus`. Locks are held through the
  commit; idempotency records after it and reserves with `check_and_set`; rollback is shielded; a second
  `next()` is refused or documented." Amends ADR-017, 018, 019, 020.
- **Research:** [02-commit-in-pipeline.research.md](02-commit-in-pipeline.research.md)
- **Status:** **decided.** The author answered every decision on 2026-10-22; each answer is a row of the
  [decision log](../docs/others/decision-log.md#12-library-behaviour) and is recorded in
  [ADR-079](../docs/adr/ADR-079-commit-inside-the-command-pipeline.md) (Proposed). The options and
  recommendations below are kept as the author saw them. Where a later row refines an option, the row wins:
  `reserve(id) -> bool` in D3b became a token (B-120); D4's "inline under other backends" became
  `anyio.CancelScope(shield=True)` (B-122); D4a now covers any exception after the commit (B-121); the
  rows B-103 to B-133 are the full set.

| Decision | Answer | Row |
|---|---|---|
| D1 Where the commit runs | A: the terminal step commits; the bus keeps `async with uow` | B-103 |
| D2 Locks through the commit | A: through the commit only, with the lease/TTL docstring note | B-104 |
| D3a When idempotency records | A: after the commit (at-least-once, gap documented); closing the gap is W3-6 (0.5.0) | B-105 |
| D3b How a command is reserved | B: a new optional Protocol, `MessageReservationStore` (`reserve`, `release`), with a fake and a contract suite | B-106 |
| D3c A concurrent duplicate | A: a new `CQRSError`, `CommandInProgressError` | B-107 |
| D4 Shielding | A: `_commit()` and `_rollback()` shielded in `AbstractUnitOfWork`, asyncio only | B-108 |
| D4a A cancelled, committed command | Yes: its result is recorded, then the cancellation re-raised | B-109 |
| D4b `_flush`, `_write_outbox` | No: not shielded | B-110 |
| D5 A second `next()` | A: refused in command pipelines with `PipelineReentryError` | B-111 |
| D6 The ordering promise | A: one promise in the slot table, with an upgrade note | B-112 |
| D7 API changes | Follows from the others, all additive; listed in ADR-079 | none |
| D8 F-6 | A: separate; ADR-068 and arc42 §11 corrected | B-113 |
| D9 Breaking or not | A behaviour change on `dev` for 0.4.0, every change with an upgrade note | B-114 |
| Shielding opt-out (raised in ADR-079) | Hooks run in their own task by default; `shield_hooks = False` runs them inline without the shield | B-115 |
| A pipeline that never reaches the handler | Commits nothing, with an upgrade note | B-116 |
| `MessageReservationStore` semantics | As ADR-079 writes them | B-117 |
| Hook failure during cancellation, the committed mark, wrapping, names | Type-only note; private mark; wrapped in `CommandExecutionError`; names accepted | B-118 |
| `shield_hooks` default and the hook task's context (PR #124) | Stays `True`; the hook task runs in the caller's context; task-keyed storage sets `False` | B-119 |
| Ownership-safe reservations (PR #124) | `reserve` returns a token (`None` if taken); `release(message_id, token)` | B-120 |
| Recording a committed command whose `next()` raises (PR #124) | On any `BaseException` once the scope is marked committed (extends B-109) | B-121 |
| The shield under anyio (PR #124) | `anyio>=4` becomes a dependency; `anyio.CancelScope(shield=True)` plus the task helper for native cancels | B-122 |
| Store-side calls and `shield_hooks` (PR #124) | Always protected, no opt-out; a store must work from any task | B-123 |
| In-process events of a committed, then cancelled command (PR #124) | Not dispatched in-process; the outbox path is the guarantee; F-10 in the backlog | B-124 |
| `reserve()` and cancellation (PR #124) | Under the same shield; a token taken while the caller was cancelled is released at once | B-125 |
| A hook's own `CancelledError` (PR #124) | That hook's failure, wrapped in `CommandExecutionError` (as B-50) | B-126 |
| `FakeUnitOfWork(fail_at=...)` (PR #124) | Fails once in the next scope, then clears; default `RuntimeError` | B-127 |
| Where anyio may be imported (PR #124 round 2) | Only `pydomain.cqrs.unit_of_work`; the architecture rule says so | B-128 |
| A post-commit `set` that raises (PR #124 round 2) | Type-only WARNING, result returned; a reservation stays until its TTL | B-129 |
| The form of a hook's own `CancelledError` (PR #124 round 2) | A private `CQRSError`, wrapped by the bus | B-130 |
| No async library running (PR #124 round 2) | The helper runs the hook inline without the anyio scope | B-131 |
| A cancellation pending before `_commit()` (PR #124 round 2) | The commit completes, then the cancellation propagates | B-132 |
| The anyio floor (PR #124 round 2) | `anyio>=4`, tested at 4.0; raised only if that fails | B-133 |

Final names (the working names of the options below changed where noted): `MessageReservationStore`
(was `CommandReservationStore`: the Protocol is keyed by message id so W2-2 can reuse it),
`CommandInProgressError`, `PipelineReentryError`, `FakeUnitOfWork(fail_at=..., failure=...)`, and the
opt-out class attribute `AbstractUnitOfWork.shield_hooks` (B-115, B-118).

---

## Phase 1: Patterns I intend to follow

- **Pattern:** the bus owns the unit of work; handlers never commit or roll back.
  - **Found in:** ADR-020 l.23, ADR-068; `cqrs/command_bus.py` l.163-198.
  - **Intend to follow:** yes. The commit moves inside the pipeline, but the bus still creates the UoW,
    builds the terminal step and decides when to commit.
- **Pattern:** the scope rolls back in `__aexit__`, once per failed dispatch; the bus never calls
  `rollback()`.
  - **Found in:** ADR-068 l.61-69; CQRS-06 tests in `tests/cqrs/test_bus.py`.
  - **Intend to follow:** yes, unless the author picks D1 option B (the terminal step owns the scope).
- **Pattern:** failures keep their cause, cancellations are never wrapped or swallowed, clean-up errors are
  type-only notes.
  - **Found in:** ADR-068, ADR-071, ADR-078; `command_bus.py` l.178-198.
  - **Intend to follow:** yes, for every new error path (commit failures, a refused second `next()`, a
    duplicate in flight).
- **Pattern:** cancellation clean-up is asyncio-aware and degrades to inline under other backends, with no
  anyio runtime dependency.
  - **Found in:** ADR-076, B-69; `behaviors.py` l.293-303; `pyproject.toml`.
  - **Intend to follow:** yes, for any shielding (D4).
- **Pattern:** Protocols stay small; every Protocol has a fake and a contract suite; public names are
  test-enforced.
  - **Found in:** `cqrs/idempotency.py`, `cqrs/locking.py`, `testing/contracts/`,
    `tests/test_package_exports.py`.
  - **Intend to follow:** yes. A new capability gets a fake method and contract cases in the same change.
- **Pattern:** behaviour changes get an upgrade note; breaking changes ship only on `release/v*`.
  - **Found in:** `docs/diataxis/how-to/upgrade/upgrade-to-next-release.md`; fix plan §5.
  - **Intend to follow:** yes (D9).
- **Pattern:** the slot order is a documented promise, not enforced at registration.
  - **Found in:** ADR-017 l.51-56, l.88; `behaviors.py` l.472-474.
  - **Intend to follow:** yes. No registration-time order checks are proposed.

---

## Phase 2: Current state and the end state the plan describes

**Today** (research §1, scratch `order_today.py`):

```
async with uow:
    logging → validation → idempotency.get → lock.acquire → handler
             ← lock.release ← idempotency.set ← logging "Completed"
    uow.commit():  _flush → _collect_and_stamp → _write_outbox → _commit
__aexit__: drain (committed) or rollback (not committed)
ctx.new_events = events          # after everything (CQRS-11)
```

**Plan's end state** (scratch prototype, research §8):

```
async with uow:
    logging → validation → idempotency (reserve) → lock.acquire → handler → uow.commit()
             ← lock.release ← idempotency (record) ← logging "Completed" / "Failed"
__aexit__: drain or rollback (shielded)
```

What the prototype shows: the order alone fixes CQRS-K1 and CQRS-K3 (counter 2, retry reruns the handler).
It does **not** fix CQRS-04, it leaves the rollback after the lock release, and a second `next()` makes
a persisted command fail with `UnitOfWorkAlreadyCommittedError`.

---

## Phase 3: Decisions for the author

Each decision: the problem with its evidence, two or three options, a recommendation. "Additive" means no
existing user code or adapter breaks.

### D1. Where the commit runs

**Problem.** The bus commits after the pipeline returns (`command_bus.py` l.172-173), so every behavior's
post-`next()` work runs before the commit: the result is cached before it (CQRS-K1) and the locks are
released before it (CQRS-K3). Research §1 and §2.

| Option | Pros | Cons |
|---|---|---|
| **A. Terminal step commits; the bus keeps the `async with`.** `register` wraps the handler: run it, then `await uow.commit()`, then set `ctx.new_events`. | Smallest change (one place in `CommandBus`); K1, K3 and CQRS-11 fixed by order; logging and metrics see commit failures; ADR-068's exit and rollback rules unchanged | The rollback and the exit drain still run after the locks are released (prototype: `lock.release, _rollback`); every behavior's error path now also sees commit failures |
| **B. Terminal step owns the whole scope**: `async with uow:` plus the commit inside the terminal step. | Rollback and drain also run under the locks; lock waits and validation run outside the scope (no storage transaction held open while waiting, for an adapter whose `__aenter__` begins one) | `ctx.uow` is not entered while outer behaviors run, so a behavior that reads through the UoW before `next()` breaks; the failure-wrapping code moves into the pipeline; larger diff to ADR-068's code |
| **C. Commit hooks**: the bus commits after the pipeline as today, and behaviors register after-commit and after-failure callbacks on `MessageContext`. | Pipeline shape and custom behaviors unchanged | New public API on `MessageContext`; the lock release leaves the behavior's own `try`, which undoes ADR-076's inline-release and hand-over design; callbacks run outside every behavior, so their errors and cancellations need their own rules |

Reordering behaviors without moving the commit fixes neither K1 nor K3, so it is not offered.

**Decided: A (B-103).** Recommendation was A. It is what the plan names, and the scratch prototype shows it fixes three rows with
the least code. The rollback-after-release gap is small: the rollback undoes uncommitted work, and B-19
already documents that locks do not protect a shared repository's buffer. Take B only if the author wants
the rollback under the lock as a promise.

### D2. Locks held through the commit

**Problem.** CQRS-K3: two locked read-modify-write commands on one key lose an update when `_commit` awaits
(probe `p_known.py`: `counter=1`; prototype: `counter=2`).

| Option | Pros | Cons |
|---|---|---|
| **A. Hold the locks through the commit** (follows from D1 A or B, nothing extra) | Fixes K3; ADR-076's release code is unchanged; a failed release after a committed command stays a WARNING, and "the command commits" stays true | Locks are held longer (the commit's duration); a provider with a lease or TTL must outlast the commit as well as the handler |
| **B. Hold them through the commit and the rollback** (needs D1 B) | Nothing runs on the key after a failure until it is rolled back | Same as D1 B |

**Decided: A (B-104).** Recommendation was A, with a sentence in `LockProvider`'s docstring and the locking how-to that a lease
must cover the commit.

### D3. Idempotency: when it records, how it reserves, what a concurrent duplicate gets

**Problem.** CQRS-K1: the result is stored before the commit, so a failed commit's retry returns a result for
work that was never persisted (research §2, every await). CQRS-04: `get` then `set` is not atomic, so two
concurrent duplicates both run (research §3: `handler runs=2`). Holding the locks through the commit does not
fix CQRS-04 (prototype: still 2).

**Research finding that changes the plan's proposal:** `check_and_set` cannot reserve a command on its own.
The Protocol has no way to release a mark (research §4), so a command whose handler or commit fails stays
marked, and every retry is refused (until a store's TTL, if it has one). Key spaces are unspecified: on the
fake, `get(id)` after `check_and_set(id)` returns `True`, so a duplicate would get `True` back as its
"result"; on the documented Redis store it returns `MISSING`. And a store shared with
`EventIdempotencyBehavior` would collide (CQRS-05, W2-2).

**D3a. When the result is recorded.**

| Option | Pros | Cons |
|---|---|---|
| **A. After the commit** (follows from D1) | Fixes K1; no store change | At-least-once: a crash or cancellation between the commit and `set` leaves no record, so a retry runs the handler again |
| **B. Inside the transaction**, written in the commit (a UoW hook beside `_write_outbox`) | Exactly-once when the store shares the database | The store must join the UoW's transaction: a new coupling between `ProcessedMessageStore` and `UnitOfWork`, and no help for a store in another system (Redis) |

**Decided: A (B-105); the gap is closed in Wave 3 (W3-6).** Recommendation was A, documented as at-least-once with the gap named. B can come later as an outbox-like
hook without undoing A.

**D3b. How a command is reserved before the handler.**

| Option | Pros | Cons |
|---|---|---|
| **A. Reserve with `check_and_set` as it is** (the plan's wording) | No new API | Unsound: a failed command stays marked and its retries are refused; the fake's shared key space returns `True` as a result |
| **B. A new optional Protocol** (working name `CommandReservationStore`, beside `ProcessedMessageStore`): `reserve(id) -> bool` (atomic, true for the first caller), `release(id)` (on failure or cancellation); the result still goes through `set`. `IdempotencyBehavior` reserves when its store implements it, and keeps today's `get`/`set` with the known-limit caveat when it does not | Additive: existing stores keep working; the fake and a contract suite gain the methods; W2-2 can reuse it for "mark only after success" | A second Protocol to document; a reservation left by a crashed process needs a TTL in the adapter (documented, not enforced); the release on cancellation needs the same care as a lock release |
| **C. Add `reserve`/`release` to `ProcessedMessageStore` itself** | One Protocol | Breaking: every existing implementation stops satisfying the `@runtime_checkable` Protocol and mypy; ships only on `release/v*` |

**Decided: B (B-106), named `MessageReservationStore`.** Recommendation was B. It is the only additive option that fixes CQRS-04 for commands without lock keys too.

**D3c. What a concurrent duplicate gets while the first runs.**

| Option | Pros | Cons |
|---|---|---|
| **A. A new error** (working name `CommandInProgressError`, a `CQRSError`), raised before the handler and wrapped like any pipeline failure | Explicit; the caller retries later and then gets the cached result; no timing parameters | Callers that retried blindly now see an error instead of a second run |
| **B. Wait for the first to finish** (poll `get` with a timeout) | The duplicate gets the same result | Needs a poll interval and timeout; holds the caller; a failed first run must wake the waiters |
| **C. Fall back to locking**: document "put the lock outermost" as the fix (the probe's `lock,idem` order gives 1 run) | No new code | Changes ADR-017's slot order; does nothing for commands without lock keys; the duplicate still opens and commits an empty scope |

**Decided: A (B-107).** Recommendation was A. Whether it is wrapped in `CommandExecutionError` follows today's rule (everything from
the pipeline is); W3-1 revisits wrapping for all errors.

### D4. Shielding the rollback, and the commit, from cancellation

**Problem.** CQRS-07: a second cancellation interrupts the rollback (research §2: `_rollback` never
returned); a cancellation after the storage commit took effect rolls back a committed scope and clears its
events, and the caller sees a failure (`db_row=True`, `CancelledError`).

| Option | Pros | Cons |
|---|---|---|
| **A. Shield `_rollback` and `_commit` in `AbstractUnitOfWork`**: run each to completion, absorbing further cancellations, then re-raise the cancellation; asyncio only, inline under other backends (as ADR-076). A `_commit` that completed marks the scope committed, so `__aexit__` drains instead of rolling back | Fixes both halves of CQRS-07; the cancellation still propagates (ADR-078); covers every `AbstractUnitOfWork` adapter | A dispatch can outlive its cancellation by the commit's or rollback's duration; a Protocol-only UoW is not covered (documented requirement); the cancelled-but-committed command reaches the caller as `CancelledError` with its events not dispatched (ADR-005 accepts that without an outbox) |
| **B. Shield the rollback only** | Smaller | A cancellation inside `_commit` still rolls back work that may have been committed |
| **C. Hand the rollback to a background task** (like ADR-076's release hand-over) | The caller is never delayed | The rollback leaves the task that owns the session, the reason ADR-076 rejected `asyncio.shield` for releases (l.137) |

**Decided: A (B-108); D4a yes (B-109); D4b no (B-110).** Recommendation was A. Two follow-ups the author should also settle:

- **D4a.** A command whose `_commit` completed but was cancelled: does `IdempotencyBehavior` still record its
  result (in a shielded `set`), so its retry does not run again? Recommendation: yes, the terminal step
  marks the context as committed and the behavior records on that mark even while the cancellation
  propagates. Without it, D3a's at-least-once gap includes every cancelled commit.
- **D4b.** Shield `_flush` and `_write_outbox` too? Recommendation: no. They run before the point of no
  return; a cancellation there rolls back cleanly (research §2: `db_row=False`).

### D5. A second `next()`

**Problem.** CQRS-21: today a behavior that calls `next()` twice runs the handler twice and commits both runs'
events. Under D1 A, a second call after a successful commit fails a persisted command with
`UnitOfWorkAlreadyCommittedError`, and a retry after a failed commit fails the same way (prototype, research
§8).

| Option | Pros | Cons |
|---|---|---|
| **A. Refuse it in command pipelines**: the terminal step raises a new error (working name `PipelineReentryError`, a `CQRSError`) on its second call, before the handler runs | Clear message; nothing runs or commits twice; retries go through a new dispatch | A retry behavior that works by accident today (handler failed before any save) stops working; behaviour change |
| **B. Allow it as a retry**: a second call rolls the scope back and starts a new one before re-running the handler | Retry behaviors work | Fights "a scope commits once" (ADR-068); after a successful commit there is nothing safe to do; idempotency and locks must be re-entered correctly |
| **C. Document it** ("call `next()` at most once") and let `UnitOfWorkAlreadyCommittedError` surface | No code | A persisted command still reports failure; the first run's events still leak into the second when the handler failed |

**Decided: A (B-111).** Recommendation was A, for `MessageKind.COMMAND` only. Query and event pipelines have no commit, so a second
call there stays allowed and undocumented as today (or documented, if the author prefers).

### D6. What custom behaviors see: the ordering promise

**Problem.** The slot table (ADR-017 l.51-56, `pipeline-behaviors.md` l.186-195) promises an order but not
what "after `next()`" means. Under D1 A it changes: a custom behavior's post-`next()` code now runs after the
commit. Work a behavior does through `ctx.uow` after `next()` (an audit row, a last save) was committed
before; it now runs after the commit and is never persisted (or raises `UnitOfWorkAlreadyCommittedError` if
it commits).

| Option | Pros | Cons |
|---|---|---|
| **A. One promise for every behavior**, added to the slot table: before `next()`, inside the scope, nothing committed; after `next()` returns, committed and `ctx.new_events` set; after `next()` raises, the handler or the commit failed and the rollback has not run yet (it runs at the exit) | Simple; matches the prototype | Post-`next()` writes through the UoW silently stop persisting: an upgrade note, and the docs say where such work goes (the handler, or a UoW hook) |
| **B. Two lists**: `behaviors` wrap the commit, a new `inner_behaviors` run between the handler and the commit | Keeps a place for post-handler work inside the transaction | New `register` parameter; two orders to teach |

**Decided: A (B-112).** Recommendation was A, with the upgrade note naming the one case that changes meaning.

### D7. Public API and Protocol changes, and whether each is additive

Following the recommendations above:

| Change | Additive? |
|---|---|
| Commit inside the terminal step; `CommandBus.register` and `dispatch` signatures unchanged | yes (behaviour change, D9) |
| `ctx.new_events` set by the terminal step after the commit (CQRS-11) | yes |
| New optional reservation Protocol (D3b B), its fake (`FakeProcessedMessageStore` implements it, or a new fake) and a contract suite | yes |
| New exceptions: duplicate in flight (D3c A), second `next()` (D5 A); exported and listed in `tests/test_package_exports.py` | yes |
| Shielded `_commit` and `_rollback` in `AbstractUnitOfWork` (D4 A) | yes (behaviour change) |
| `FakeUnitOfWork` commit-failure injection (FAKE-04 rest): keyword arguments, for example `fail_on="flush" \| "outbox" \| "commit"` with an exception to raise (named `fail_at`, with `"write_outbox"`, and `failure` in ADR-079, B-118) | yes |
| Docstring requirement: a `LockProvider` lease covers the commit (D2) | yes |

Not proposed: changing `ProcessedMessageStore`'s four methods (D3b C), `MessageContext` fields, or the
`UnitOfWork` Protocol.

### D8. The relation to F-6 (scope-keyed event buffers)

**Problem.** ADR-068 l.171-173 and arc42 §11 l.254 say the locks are released "before the commit and the
exit (CQRS-K3, fixed by W2-1)", and F-6 "pairs with W2-1". Holding locks through the commit does not make a
shared repository safe: commands on different keys still share its buffer, and the exit drain runs after the
release (D1 A).

| Option | Pros | Cons |
|---|---|---|
| **A. Keep F-6 separate**; W2-1 corrects the ADR-068 and arc42 sentences to say what it fixes (K3) and what stays (the shared-buffer rule of B-19) | W2-1 stays bounded; no change to `Repository` | The "build the repository per UoW" rule stays |
| **B. Bundle F-6 into W2-1** | One change to the commit path | Changes how every `Repository` buffers events; a much larger cluster |

**Decided: A (B-113).** Recommendation was A.

### D9. Which changes are behaviour changes needing an upgrade note

All of these are behaviour changes in `upgrade-to-next-release.md` (none removes a public name):

1. Locks are held through the commit (D2).
2. Idempotency records the result after the commit: a command whose commit failed runs again on retry (D3a).
3. A concurrent duplicate of an in-flight command gets an error instead of running (D3b, D3c), only with a
   store that implements the reservation Protocol.
4. A behavior's post-`next()` code runs after the commit; its writes through the UoW are no longer
   committed; `LoggingBehavior` and custom behaviors see commit failures as failures of `next()` (D6).
5. A second `next()` in a command pipeline raises (D5).
6. `ctx.new_events` is populated for behaviors (D7).
7. A cancellation during `_commit` or `_rollback` waits for it to finish; a committed scope is no longer rolled
   back after a cancellation (D4).

**Decided (B-114): a behaviour change on `dev` for 0.4.0, with a prominent upgrade note.** The question was: is item 4 (post-`next()` writes no longer committed) breaking under the fix
plan's §5 rules, so that W2-1 must ship on `release/v*`, or a behaviour change on `dev` for 0.4.0? My
recommendation: a behaviour change with a prominent upgrade note, because no documented behavior uses `ctx.uow`
at all (research §10).

---

## Phase 4: Where this constrains other clusters

- **W2-2 (DCE-131).** If D3b B is chosen, W2-2's "mark only after success" can reuse `reserve`/`release`
  for events, and its "separate key spaces" must cover the command reservations too. W2-2 should not
  change `IdempotencyBehavior`'s reservation semantics.
- **W2-10 (tracing).** Under D1 A a tracing behavior at slot 0 spans the commit; W2-10's public tracing
  channel must be readable inside the terminal step, where the commit stamps events.
- **W3-1 (error hierarchy).** The new errors of D3c and D5 are `CQRSError`s; W3-1 decides how they are
  wrapped, like every other error.
- **W2-6.** Untouched: the `FakeRepository` part of FAKE-04 stays there.

## Phase 5: Scope

**In:** the rows CQRS-K1, CQRS-K3, CQRS-04, CQRS-07, CQRS-21, CQRS-11 and FAKE-04 (commit-failure
injection); ADR-079 amending ADR-017 to 020 (and correcting ADR-068 l.171-173 and ADR-076 l.189); tests of
fix plan §9.2; the arc42 and Diataxis pages of §9.2; the upgrade notes.

**Out:** F-6; event idempotency (W2-2); the tracing channel (W2-10); error wrapping (W3-1); exactly-once
idempotency inside the transaction (D3a B); registration-time checks of the slot order.
