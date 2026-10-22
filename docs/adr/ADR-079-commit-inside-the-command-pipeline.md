# ADR-079: Commit Inside the Command Pipeline

## Status

Proposed. When Accepted, amends [ADR-017](ADR-017-onion-style-pipeline-behaviors.md),
[ADR-018](ADR-018-missing-sentinel-idempotency.md), [ADR-019](ADR-019-sorted-lock-keys-deadlock-prevention.md)
and [ADR-020](ADR-020-commandbus-owns-uow-lifecycle.md), and corrects one sentence each of
[ADR-068](ADR-068-rollback-drains-event-buffers.md) and
[ADR-076](ADR-076-release-every-lock-under-cancellation-and-failing-releases.md). The amendment notes go into
those ADRs when this one is Accepted, not before.

## Date

2026-10-22

## Context

`CommandBus.dispatch()` runs the pipeline inside `async with uow:` and commits after the whole pipeline returns
(ADR-020). Every behavior's work after `next()` therefore runs before the commit. The 0.3.0 audit found what
follows from that (YouTrack DCE-142, fix plan cluster W2-1; facts and scratch evidence in
`design-docs/02-commit-in-pipeline.research.md`):

1. **CQRS-K1.** `IdempotencyBehavior` stores the result before the commit. When the commit fails or is
   cancelled, at any of its awaits, a retry with the same `command_id` gets the stored result back, the handler
   does not run again, and nothing was persisted.
2. **CQRS-K3.** `AggregateLockingBehavior` releases its locks before the commit. Two locked read-modify-write
   commands on one key lose an update when the commit awaits (`counter=1` instead of 2).
3. **CQRS-04.** `IdempotencyBehavior` does `get`, then `set`, with the handler in between. Two concurrent
   dispatches of one `command_id` both miss and both run the handler. Holding the locks through the commit does
   not fix this, and `ProcessedMessageStore.check_and_set` cannot reserve a command either: the Protocol has no
   way to release a mark, so a command that failed would stay marked and its retries would be refused.
4. **CQRS-07.** Nothing is shielded. A second cancellation interrupts the rollback; a cancellation after the
   storage commit took effect still rolls the scope back, clears its events, and reports a failure.
5. **CQRS-21.** A behavior that calls `next()` twice runs the handler twice and commits both runs' events.
6. **CQRS-11.** `ctx.new_events` is set after the pipeline and the commit, so no behavior ever sees it.
7. **FAKE-04 (rest).** `FakeUnitOfWork` has no way to make a commit fail, so none of the above can be tested
   with the shipped fakes.

The slot table (ADR-017) promises an order but not what "after `next()`" means, and no document says whether a
behavior may call `next()` more than once.

## Decision

The author decided each point on 2026-10-22 ([decision log](../others/decision-log.md#12-library-behaviour)
B-103 to B-114). The alignment with every option offered is
`design-docs/02-commit-in-pipeline.alignment.md`.

**1. The commit runs in the pipeline's terminal step (amends ADR-020; B-103).** `CommandBus.register` builds
each command pipeline with a terminal step that awaits the handler, then `uow.commit()`, then sets
`ctx.new_events` from `uow.collect_events()`. `ctx.handler` stays the registered handler, so logs name it. The
bus still owns the unit of work: it creates it, enters `async with uow:` before the first behavior, and its
`__aexit__` drains a committed scope or rolls back one that did not commit, once (ADR-068). The bus no longer
commits after the pipeline. A pipeline that returns without reaching the terminal step (a cached duplicate, a
behavior that short-circuits) commits nothing, and its scope rolls back on exit. Handlers still never commit
(ADR-020). Query and event pipelines are unchanged.

**2. Locks are held through the commit (amends ADR-019 through ADR-076; B-104).** This follows from point 1
with no change to `AggregateLockingBehavior`: its releases run after `next()`, which now includes the commit.
The rollback and the exit drain still run after the releases. The `LockProvider` docstring and the locking
how-to say that a lease or TTL must cover the commit as well as the handler.

**3. Idempotency records the result after the commit (amends ADR-018; B-105).** `IdempotencyBehavior`'s `set`
runs after `next()` returns, so after the commit. This is at-least-once: a crash or an error between the commit
and the record leaves no record, and a retry runs the handler again. The docs name the gap. Closing it
(exactly-once, the record committed with the work) is planned for 0.5.0 (fix plan W3-6).

**4. A new optional Protocol reserves a command (B-106).** `MessageReservationStore` lives beside
`ProcessedMessageStore` in `pydomain.cqrs.idempotency`:

```python
@runtime_checkable
class MessageReservationStore(Protocol):
    async def reserve(self, message_id: UUID) -> bool:
        """Atomically reserve *message_id*; True only for the one caller that took it."""

    async def release(self, message_id: UUID) -> None:
        """Drop a reservation whose message was not completed, so a retry can reserve it again."""
```

A reservation is completed by `ProcessedMessageStore.set(message_id, result)`; after that, `get` returns the
result. A reservation is not a result: `get` returns `MISSING` for an id that is only reserved, and `reserve`
never marks an id that `check_and_set` uses (reservations and event marks are separate key spaces). A
reservation left by a crashed process is freed by the adapter's TTL, which the docs ask for and nothing
enforces. When the behavior's store implements both Protocols, `IdempotencyBehavior`:

1. returns the cached result if `get` finds one;
2. calls `reserve`; if it did not get the reservation, calls `get` again and returns the result if the first
   dispatch completed in between, and otherwise raises `CommandInProgressError` (point 5);
3. runs `next()`; on return, records the result with `set`;
4. on any `BaseException` from `next()`, calls `release` and re-raises; a failing `release` is a type-only note
   on the error (ADR-071), and a `release` after a cancellation runs shielded, as the rollback (point 6).

A store that implements only `ProcessedMessageStore` keeps today's `get`/`set` flow, with the known-limit
caveat that two concurrent duplicates can both run. `FakeProcessedMessageStore` implements both Protocols, and a
new contract suite, `MessageReservationStoreContract`, pins them. This is additive: existing stores keep
satisfying `ProcessedMessageStore`.

**5. A concurrent duplicate gets `CommandInProgressError` (B-107).** A new `CQRSError`, raised before the handler
runs, so nothing of the duplicate is committed. It propagates like any pipeline failure: the caller of
`dispatch()` gets `CommandExecutionError` with it as `__cause__` (W3-1 revisits wrapping for every error). Its
message names the command type only. A retry after the first dispatch completed gets the cached result.

**6. `_commit()` and `_rollback()` are shielded from cancellation (B-108, B-109, B-110).** In
`AbstractUnitOfWork`, `commit()` runs its `_commit()` hook and `rollback()` its `_rollback()` hook to completion
when the calling task is cancelled while they run:

- Under asyncio, the hook runs in a task that the caller awaits through `asyncio.shield`. Further cancellations
  are absorbed until the hook ends; then the cancellation is re-raised. If the hook raised as well, the
  cancellation propagates with a type-only note naming the hook's error (ADR-078).
- With no asyncio task running (trio through anyio), the hook is awaited inline, unshielded, as ADR-076 does for
  releases.
- A `_commit()` that completed marks the scope committed before the cancellation is re-raised, so `__aexit__`
  drains instead of rolling back, and the commit is not reported as a failure of the storage: the caller sees
  the cancellation.
- When the commit completed and the cancellation is re-raised through the terminal step, `IdempotencyBehavior`
  still records the result, shielded the same way, and then re-raises (B-109). The terminal step marks the
  context as committed for this; the mark is private.
- `_flush()` and `_write_outbox()` are not shielded (B-110): they run before the point of no return, and a
  cancellation there rolls back cleanly.

A unit of work that implements only the `UnitOfWork` Protocol is not covered; its docstring asks it to shield
its own storage commit and rollback.

**7. A second `next()` in a command pipeline is refused (B-111).** The terminal step raises
`PipelineReentryError`, a new `CQRSError`, when it is called a second time in one dispatch, before the handler
runs again. A first run that committed stays committed. Query and event pipelines are unchanged: a second
`next()` there still runs the handler again.

**8. One ordering promise for behaviors (amends ADR-017; B-112).** The slot table gains what each side of
`next()` means in a command pipeline:

| Point | What a behavior sees |
|---|---|
| Before `next()` | Inside the unit-of-work scope; nothing committed; `ctx.new_events` empty |
| After `next()` returns | The handler ran and the commit succeeded; `ctx.new_events` holds the committed events |
| After `next()` raises | The handler or the commit failed (or a refusal of points 5 and 7); the rollback has not run yet: it runs at the exit |

Work a behavior does through `ctx.uow` after `next()` is no longer committed. It belongs in the handler or in a
unit-of-work hook.

**9. F-6 stays separate; two sentences are corrected (B-113).** Holding the locks through the commit does not
make a shared repository safe: commands on other keys still share its buffer, and the exit drain runs after the
release. ADR-068's sentence that `AggregateLockingBehavior` "releases them before the commit and the exit
(CQRS-K3, fixed by W2-1)" becomes: it holds them through the commit (this ADR), still releases them before the
exit, and still does not protect a shared repository's buffer (B-19). ADR-076's "holding them through the commit
is W2-1" points here. Scope-keyed buffers stay backlog Feature F-6.

**10. Public API (no decision-log row; it follows from points 1 to 8).** Every change is additive:

| Change | Where |
|---|---|
| The commit in the terminal step; `register` and `dispatch` signatures unchanged | `cqrs/command_bus.py` |
| `ctx.new_events` populated for behaviors | `cqrs/command_bus.py` |
| `MessageReservationStore` Protocol | `cqrs/idempotency.py`, exported from `pydomain.cqrs` |
| `FakeProcessedMessageStore` implements it; `MessageReservationStoreContract` | `pydomain.testing`, `pydomain.testing.contracts` |
| `CommandInProgressError`, `PipelineReentryError` (both `CQRSError`) | `cqrs/exceptions.py`, exported |
| Shielded `_commit()` and `_rollback()` | `cqrs/unit_of_work.py` |
| `FakeUnitOfWork(fail_at=..., failure=...)`: makes `_flush`, `_write_outbox` or `_commit` raise `failure` (FAKE-04) | `testing/fake_unit_of_work.py` |
| Docstrings: a `LockProvider` lease covers the commit; a Protocol-only `UnitOfWork` shields its own hooks | `cqrs/locking.py`, `cqrs/unit_of_work.py` |

Not changed: the four methods of `ProcessedMessageStore`, the `MessageContext` fields, the `UnitOfWork`
Protocol's methods, the slot numbers.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| The terminal step owns the whole scope (`async with uow` inside the pipeline) | Rejected by the author on 2026-10-22 (B-103): behaviors before `next()` would lose an entered unit of work, and ADR-068's failure handling would move into the pipeline |
| After-commit hooks on `MessageContext`, the bus committing after the pipeline | Rejected by the author on 2026-10-22 (B-103): new public API, and the lock release would leave the behavior's own `try`, undoing ADR-076 |
| Hold the locks through the rollback too | Rejected by the author on 2026-10-22 (B-104): needs the scope inside the pipeline |
| Record the result inside the transaction now | Rejected by the author on 2026-10-22 for 0.4.0 (B-105): the store would join the unit of work; planned for 0.5.0 as W3-6 |
| Reserve with `check_and_set` as it is | Rejected by the author on 2026-10-22 (B-106): a failed command would stay marked, and the fake would return `True` as a result |
| Add `reserve` and `release` to `ProcessedMessageStore` | Rejected by the author on 2026-10-22 (B-106): breaking for every existing store |
| Make a duplicate wait for the first result | Rejected by the author on 2026-10-22 (B-107): needs a poll interval and a timeout, and holds the caller |
| Document "lock outermost" as the fix for duplicates | Rejected by the author on 2026-10-22 (B-107): changes ADR-017's order and does nothing for commands without lock keys |
| Shield the rollback only | Rejected by the author on 2026-10-22 (B-108): a cancellation inside `_commit()` would still roll back committed work |
| Hand the rollback to a background task | Rejected by the author on 2026-10-22 (B-108): the caller would return before the rollback ends |
| Leave a cancelled, committed command unrecorded | Rejected by the author on 2026-10-22 (B-109): its retry would run the handler again |
| Shield `_flush()` and `_write_outbox()` too | Rejected by the author on 2026-10-22 (B-110) |
| Allow a second `next()` as a retry in a new scope | Rejected by the author on 2026-10-22 (B-111): a scope commits once (ADR-068), and after a commit nothing is safe to repeat |
| Document "call `next()` at most once" only | Rejected by the author on 2026-10-22 (B-111): a persisted command would still be reported as failed with `UnitOfWorkAlreadyCommittedError` |
| A second list of behaviors between the handler and the commit | Rejected by the author on 2026-10-22 (B-112): two orders to teach for a case no documented behavior has |
| Bundle scope-keyed buffers (F-6) | Rejected by the author on 2026-10-22 (B-113): changes how every repository buffers events |
| Ship it as a breaking change on `release/v0.5` | Rejected by the author on 2026-10-22 (B-114) |

## Consequences

### Positive

- A failed or cancelled commit is never reported as a cached success: its retry runs the handler (CQRS-K1).
- Locked read-modify-write commands on one key no longer lose updates (CQRS-K3).
- With a store that implements `MessageReservationStore`, one `command_id` runs its handler at most once at a
  time, with or without lock keys (CQRS-04).
- A rollback is not cut short by a second cancellation, and a commit that took effect is not rolled back after
  a cancellation (CQRS-07).
- A second `next()` fails loudly instead of committing two runs (CQRS-21); behaviors can read
  `ctx.new_events` (CQRS-11); `LoggingBehavior` and user behaviors see commit failures as failures.
- `FakeUnitOfWork` can make a commit fail at each of its awaits, so users can test their failure paths
  (FAKE-04).

### Negative

- Behaviour changes on `dev` for 0.4.0 (B-114), each with an upgrade note:
  1. locks are held through the commit;
  2. the result is recorded after the commit, so a command whose commit failed runs again on retry;
  3. a concurrent duplicate gets `CommandInProgressError`, with a reserving store;
  4. a behavior's code after `next()` runs after the commit, its writes through the unit of work are no longer
     committed, commit failures reach its error path, and a pipeline that never reaches the handler commits
     nothing;
  5. a second `next()` in a command pipeline raises `PipelineReentryError`;
  6. `ctx.new_events` is set for behaviors;
  7. a cancellation during `_commit()` or `_rollback()` waits for it, and a committed scope is no longer rolled
     back after a cancellation.
- At-least-once remains: a crash between the commit and the record runs the handler again on retry. Planned for
  0.5.0 (W3-6).
- Under asyncio, `_commit()` and `_rollback()` run in a separate task (a copy of the caller's context). A storage
  client that refuses to be used from a task other than the one that opened it refuses these calls; ADR-076
  kept lock releases in the acquiring task for this reason. Whether such adapters get a way to opt out is
  open while this ADR is Proposed.
- A cancelled dispatch can outlive its cancellation by the duration of the commit or the rollback.
- A reservation left by a crashed process blocks its `command_id` with `CommandInProgressError` until the
  adapter's TTL frees it.

### Neutral

- Locks are held for the commit's duration as well as the handler's.
- The rollback and the exit drain still run after the locks are released, and sharing a repository between
  concurrent dispatches is still unsafe (B-19, F-6).
- A Protocol-only unit of work and a store without `MessageReservationStore` keep today's behaviour for
  shielding and duplicates.
- Constraints on other clusters: W2-2 can reuse `MessageReservationStore` for "mark only after success" and keeps
  the key spaces of point 4 separate; under W2-10 a tracing behavior at slot 0 spans the commit; W3-1 decides how
  `CommandInProgressError` and `PipelineReentryError` are wrapped.

## References

- `design-docs/02-commit-in-pipeline.research.md`, `design-docs/02-commit-in-pipeline.alignment.md`,
  `design-docs/02-commit-in-pipeline.frame.md`
- [Decision log](../others/decision-log.md#12-library-behaviour), B-103 to B-114; B-19 and F-6
- `docs/others/bug-audit-findings.md`: CQRS-K1, CQRS-K3, CQRS-04, CQRS-07, CQRS-11, CQRS-21, FAKE-04
- `docs/others/bug-fix-plan.md` §9.1 (W2-1), §9.2, §10 (W3-6)
- arc42 [§9.30](../arch42/09-design-decisions.md#930-commit-inside-the-command-pipeline)
- [ADR-005](ADR-005-publish-after-commit.md), [ADR-062](ADR-062-eventbus-application-layer-peer.md),
  [ADR-071](ADR-071-library-failure-paths-carry-no-payload-values.md),
  [ADR-078](ADR-078-cleanup-errors-stored-versions-and-per-class-configure.md)
