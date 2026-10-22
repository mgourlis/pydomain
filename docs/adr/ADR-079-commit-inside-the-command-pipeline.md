# ADR-079: Commit Inside the Command Pipeline

## Status

Proposed. When Accepted, amends [ADR-017](ADR-017-onion-style-pipeline-behaviors.md),
[ADR-018](ADR-018-missing-sentinel-idempotency.md), [ADR-019](ADR-019-sorted-lock-keys-deadlock-prevention.md)
and [ADR-020](ADR-020-commandbus-owns-uow-lifecycle.md), and corrects sentences of
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
B-103 to B-133). The alignment with every option offered is
`design-docs/02-commit-in-pipeline.alignment.md`; the review of PR #124 added B-119 to B-133.

**1. The commit runs in the pipeline's terminal step (amends ADR-020; B-103, B-116).** `CommandBus.register`
builds each command pipeline with a terminal step that awaits the handler, then `uow.commit()`, then sets
`ctx.new_events` from `uow.collect_events()`; it sets them too when `commit()` raises after a completed
`_commit()` (the committed mark, point 6). The terminal step is the bus's own, not `MessagePipeline`'s
generic terminal, which query and event pipelines (and pipelines built without a unit of work) keep using.
`ctx.handler` stays the registered handler, so logs name it. The bus still owns the unit of work: it creates
it, enters `async with uow:` before the first behavior, and its `__aexit__` drains a committed scope or rolls
back one that did not commit, once (ADR-068). The bus no longer commits after the pipeline. A pipeline that
returns without reaching the terminal step (a cached duplicate, a behavior that short-circuits) commits
nothing, and its scope rolls back on exit (B-116). Handlers still never commit (ADR-020). Query and event
pipelines are unchanged.

**2. Locks are held through the commit (amends ADR-019 as amended by ADR-076; B-104).** This follows from
point 1 with no change to `AggregateLockingBehavior`: its releases run after `next()`, which now includes the
commit. The rollback and the exit drain still run after the releases. The `LockProvider` docstring and the
locking how-to say that a lease or TTL must cover the commit as well as the handler. A nested command commits
and releases its own keys before the outer command commits and releases its keys.

**3. Idempotency records the result after the commit (amends ADR-018; B-105).** `IdempotencyBehavior`'s `set`
runs after `next()` returns, so after the commit. This is at-least-once. A retry runs the handler again when no
record was written although the work was committed: after a crash between the commit and the record, and
after an error that a `_commit()` raises once the storage commit took effect (an ambiguous commit failure:
the scope rolls back, the caller sees a failure, and the retry applies the work a second time). The docs name
both. Closing the gap (exactly-once, the record committed with the work) is planned for 0.5.0 (fix plan W3-6).

**4. A new optional Protocol reserves a command (B-106, B-117, B-120, B-121, B-123, B-125).** `MessageReservationStore`
lives beside `ProcessedMessageStore` in `pydomain.cqrs.idempotency`:

```python
@runtime_checkable
class MessageReservationStore(Protocol):
    async def reserve(self, message_id: UUID) -> Hashable | None:
        """Atomically reserve *message_id*; return a token, or None if it is already reserved."""

    async def release(self, message_id: UUID, token: Hashable) -> None:
        """Drop the reservation *token* took, if it still holds *message_id*; never another one."""
```

The token makes `release` ownership-safe (B-120): a release whose reservation already ended (freed by a TTL,
then taken by another dispatch) frees nothing. A reservation is completed by
`ProcessedMessageStore.set(message_id, result)`; after that, `get` returns the result. A reservation is not a
result: `get` returns `MISSING` for an id that is only reserved, and reservations, results and the marks of
`check_and_set` are separate key spaces (B-117). A reservation left by a crashed process is freed by the
adapter's TTL, which the docs ask for and nothing enforces. When the behavior's store implements both Protocols,
`IdempotencyBehavior`:

1. returns the cached result if `get` finds one;
2. calls `reserve`, protected as point 6 describes (B-125); if the caller was cancelled while it ran, releases
   the new token at once and re-raises, so no reservation leaks; if it got no token, calls `get` again and returns the result if the first dispatch
   completed in between, and otherwise raises `CommandInProgressError` (point 5);
3. runs `next()`; on return, records the result with `set`, protected as point 6 describes (B-123), which
   completes the reservation. A `set` that raises is logged as a type-only WARNING and the result is still
   returned, as for a failed lock release after success; a reserving store keeps the reservation until its
   TTL, so an immediate duplicate is still refused, and a retry after the TTL runs the handler again (B-129);
4. when `next()` raises any `BaseException` and the context carries the committed mark (point 6), records the
   result with `set` the same way (a `set` that raises is logged as in step 3), then re-raises the original
   exception: a committed command is not run again (B-121);
5. when `next()` raises and the context is not marked committed, calls `release(message_id, token)`, protected
   the same way, and re-raises; a failing `release` is a type-only note on the error (ADR-071).

A store that implements only `ProcessedMessageStore` keeps the `get`/`set` flow, with the record after the
commit (point 3), steps 3 and 4 for `set`, and the known-limit caveat that two concurrent duplicates can both
run. `FakeProcessedMessageStore` implements both Protocols, and a new contract suite,
`MessageReservationStoreContract`, pins them. This is additive: existing stores keep satisfying
`ProcessedMessageStore`.

The store-side calls (`release`, and `set` after a commit) are always protected, with no opt-out (B-123), so a
store must work from any task: a connection pool (`redis.asyncio`, an asyncpg pool, an `AsyncEngine` with a
short session per call), never the unit of work's task-scoped session. The `ProcessedMessageStore` and
`MessageReservationStore` docstrings say so. `reserve` is protected the same way (B-125).

**5. A concurrent duplicate gets `CommandInProgressError` (B-107, B-118).** A new `CQRSError`, raised before the
handler runs, so nothing of the duplicate is committed. It propagates like any pipeline failure: the caller of
`dispatch()` gets `CommandExecutionError` with it as `__cause__` (W3-1 revisits wrapping for every error). Its
message names the command type only. A retry after the first dispatch completed gets the cached result.

**6. `_commit()` and `_rollback()` are shielded from cancellation (B-108 to B-110, B-115, B-118, B-119, B-122,
B-126, B-128, B-130 to B-133).**
In `AbstractUnitOfWork`, `commit()` runs its `_commit()` hook and `rollback()` its `_rollback()` hook to
completion when the calling task is cancelled while they run. `anyio>=4` becomes a runtime dependency for this
(B-122); P3 runs the shield tests at the declared floor (`anyio==4.0`) and raises the floor to the lowest version
that passes if they fail, stated in the upgrade note (B-133). One private helper in `pydomain.cqrs.unit_of_work`
does it, and the store-side calls of point 4 use the same helper; it is the only runtime module that imports
anyio (`behaviors.py` imports the helper), and `tests/test_architecture.py` allows exactly that, with pytest
still banned at runtime (B-128).

- The call runs inside `anyio.CancelScope(shield=True)`, which absorbs a cancellation of an anyio cancel scope
  (and under trio, any cancellation) inline, in the same task, without spinning.
- A native asyncio `task.cancel()` is not stopped by that scope, so under asyncio the call also runs in its own
  task. The task is built with the constructor, `asyncio.Task(coro, loop=loop, context=ctx)` with the caller's
  context, so context variables set on either side stay visible (B-119), and it never starts eagerly:
  `asyncio.create_task(..., context=...)` under `asyncio.eager_task_factory` fails with "cannot enter context"
  on Python 3.12 to 3.14 and can leave the loop's state corrupted. The caller waits on a private future that
  the inner task's done callback sets, not on `asyncio.shield`, which on 3.14 logs the hook's exception with
  its message and traceback ("exception in shielded future"), content in a log against ADR-071. Cancellations
  of the caller are absorbed until the inner task ends.
- What follows depends on the backend: under a native asyncio cancellation the helper re-raises the
  `CancelledError` once the call ended; under an anyio cancel scope it does not re-raise, and anyio delivers
  the cancellation again at the next checkpoint, so a dispatch with no checkpoint left can return normally,
  committed and recorded (`cancelled_caught` is then false).
- When no async library is detected (sniffio finds none, as when a coroutine is driven by hand), the helper
  runs the call inline without the anyio scope, so hand-driven use and the B-69 tests keep working (B-131).
- A `CancelledError` that reaches the caller while the inner task is not itself cancelled belongs to the
  caller, including one already pending when the helper starts: it is never swallowed, and it propagates once
  the call ended. A cancellation requested before `_commit()` starts, and not delivered at an earlier await,
  still lets the commit complete; the cancellation then propagates and the result is recorded (B-121). Once
  `_commit()` starts it is the point of no return (B-132). B-110's "a cancellation in `_flush()` or
  `_write_outbox()` rolls back" holds for one delivered while they run.
- A `CancelledError` that a hook raises itself while the caller is not cancelled is that hook's failure, as
  ADR-076's B-50 is for a release (B-126): the helper raises a private `CQRSError` from it, the scope is not
  marked committed, and the bus wraps that error in `CommandExecutionError`, so the hook's `CancelledError` is
  `__cause__.__cause__`; a direct user of the unit of work gets the private `CQRSError` (B-130).
- If the hook raised an error while the caller was being cancelled, the cancellation propagates with a type-only
  note naming the hook's error (ADR-078, B-118).
- A unit of work whose storage is tied to the task that opened it opts out with the class attribute
  `shield_hooks: ClassVar[bool] = True` on `AbstractUnitOfWork`, set to `False` in the subclass: its hooks then
  run inline in the calling task, with no shield of either kind (B-115). Storage keyed by `current_task()`, for
  example SQLAlchemy's `async_scoped_session(scopefunc=current_task)`, must set it: in a hook task it gets a
  different session and silently loses the writes (B-119). The store-side calls of point 4 do not depend on
  `shield_hooks` (B-123).
- A `_commit()` that completed marks the scope committed before the cancellation is re-raised, so `__aexit__`
  drains instead of rolling back, and the caller sees the cancellation, not a storage failure.
- `_flush()` and `_write_outbox()` are not shielded (B-110): they run before the point of no return.

Review ⑨ of PR #124 checked this design on Python 3.12.3, 3.13.16 and 3.14.8 with anyio 4.15.1: 0.01 to 0.02 s
of CPU over a 0.5 s commit under an anyio scope, a pending cancellation kept, every reservation scenario passing
with tokens, and no window left where the record is lost or written twice under cancellation.

**The committed mark (B-118, B-121).** After `uow.commit()` returns, the terminal step sets `ctx.new_events`
and stores the handler's result on the context in a private attribute, set with `setattr` as the bus already
sets the tracing ids, not a `MessageContext` field and not a `metadata` key. When `commit()` raises, the
terminal step reads `AbstractUnitOfWork`'s private committed flag to learn whether the commit completed (a
cancellation after a completed `_commit()`), and then sets `ctx.new_events` and stores the mark too; for a unit
of work that implements only the Protocol, a `commit()` that raises counts as not committed.
`IdempotencyBehavior` reads the mark (point 4, steps 3 and 4); nothing else does.

A unit of work that implements only the `UnitOfWork` Protocol is not covered by the shield; its docstring asks
it to shield its own storage commit and rollback.

**7. A second `next()` in a command pipeline is refused (B-111).** The terminal step raises
`PipelineReentryError`, a new `CQRSError`, when it is called a second time in one dispatch, before the handler
runs again. A first run that committed stays committed. Query and event pipelines are unchanged: a second
`next()` there still runs the handler again.

**8. One ordering promise for behaviors (amends ADR-017; B-112, B-116, B-121).** The slot table gains what each
side of `next()` means in a command pipeline:

| Point | What a behavior sees |
|---|---|
| Before `next()` | Inside the unit-of-work scope; nothing committed; `ctx.new_events` empty |
| After `next()` returns, the handler reached | The handler ran and the commit succeeded; `ctx.new_events` holds the committed events |
| After `next()` returns, the handler not reached | An inner behavior short-circuited (a cached duplicate): nothing was committed and nothing will be; the scope rolls back at the exit (B-116); `ctx.new_events` empty |
| After `next()` raises, not committed | The handler or the commit failed (or a refusal of points 5 and 7); the rollback has not run yet: it runs at the exit |
| After `next()` raises, committed | The commit completed and something after it raised (a cancellation, a failed lock release that re-raised a cancellation); no rollback runs; the terminal step set `ctx.new_events` to the committed events, which are not dispatched in-process (point 10) |

An empty `ctx.new_events` does not tell an outer behavior whether a commit happened: a commit can produce no
events, and the committed mark is private (B-118). Work a behavior does through `ctx.uow` after `next()` is no
longer committed. It belongs in the handler or in a unit-of-work hook.

**9. F-6 stays separate; stale sentences are corrected (B-113).** Holding the locks through the commit does not
make a shared repository safe: commands on other keys still share its buffer, and the exit drain runs after the
release. ADR-068's sentence that `AggregateLockingBehavior` "releases them before the commit and the exit
(CQRS-K3, fixed by W2-1)" becomes: it holds them through the commit (this ADR), still releases them before the
exit, and still does not protect a shared repository's buffer (B-19). ADR-076's "holding them through the commit
is W2-1" points here, and its "the result is returned, so the bus commits" (l.74-75) becomes "the commit has
already happened". Scope-keyed buffers stay backlog Feature F-6. The same sentences in arc42, the decision
log's F-6 row and the Diataxis pages are corrected with the implementation (frame, phase 1).

**10. In-process events of a command that committed and then failed (B-124).** A command whose commit completed
and whose dispatch then raises (a cancellation, any error after the commit) does not return its events, so
`MessageBus` never hands them to in-process handlers, and a retry returns the cached result without them. The
guarantee that projections see every committed change comes from the outbox path (the relay, the inbound
gateway, or a catch-up subscription), which also survives crashes; in-process handlers stay best-effort
(ADR-046). The docs say so. A local outbox-fed dispatcher for in-process handlers is backlog Feature F-10.

**11. Public API (no decision-log row of its own; it follows from points 1 to 10).** Every change is additive; one runtime
dependency is added:

| Change | Where |
|---|---|
| The commit in the bus's terminal step; `register` and `dispatch` signatures unchanged | `cqrs/command_bus.py` |
| `ctx.new_events` populated for behaviors | `cqrs/command_bus.py` |
| `MessageReservationStore` Protocol (`reserve` returns a token) | `cqrs/idempotency.py`, exported from `pydomain.cqrs` |
| `FakeProcessedMessageStore` implements it; `MessageReservationStoreContract` | `pydomain.testing`, `pydomain.testing.contracts` |
| `CommandInProgressError`, `PipelineReentryError` (both `CQRSError`) | `cqrs/exceptions.py`, exported |
| Shielded `_commit()` and `_rollback()`; the `shield_hooks` class attribute to opt out | `cqrs/unit_of_work.py` |
| `anyio>=4` in `dependencies`, tested at the floor (B-133); anyio allowed only in `pydomain.cqrs.unit_of_work` (B-128) | `pyproject.toml`, `tests/test_architecture.py` |
| `FakeUnitOfWork(fail_at=..., failure=None)`: makes `_flush`, `_write_outbox` or `_commit` raise `failure` once, in the next scope that reaches that hook, then clears itself; with `failure=None` it raises a new `RuntimeError` each time (FAKE-04, B-127) | `testing/fake_unit_of_work.py` |
| Docstrings: a `LockProvider` lease covers the commit; a Protocol-only `UnitOfWork` shields its own hooks; a store works from any task | `cqrs/locking.py`, `cqrs/unit_of_work.py`, `cqrs/idempotency.py` |

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
| `release(message_id)` without a token, documented as "must be ownership-safe" | Rejected by the author on 2026-10-22 (B-120): every adapter would have to invent the token itself |
| Accept the TTL race of an unowned release | Rejected by the author on 2026-10-22 (B-120): a late release would let a second dispatch run alongside a third |
| Make a duplicate wait for the first result | Rejected by the author on 2026-10-22 (B-107): needs a poll interval and a timeout, and holds the caller |
| Document "lock outermost" as the fix for duplicates | Rejected by the author on 2026-10-22 (B-107): changes ADR-017's order and does nothing for commands without lock keys |
| Shield the rollback only | Rejected by the author on 2026-10-22 (B-108): a cancellation inside `_commit()` would still roll back committed work |
| Hand the rollback to a background task | Rejected by the author on 2026-10-22 (B-108): the caller would return before the rollback ends |
| Always shield, with no opt-out | Rejected by the author on 2026-10-22 (B-115): a unit of work whose storage is tied to its task could not be used |
| Run the hooks inline with no shield | Rejected by the author on 2026-10-22 (B-115): a second cancellation would cut the rollback short again |
| `shield_hooks = False` by default (opt in) | Rejected by the author on 2026-10-22 (B-119): most adapters would keep the CQRS-07 defect |
| `shield_hooks = True` with only an upgrade note on task-keyed storage | Rejected by the author on 2026-10-22 (B-119): the lost writes are silent, so the docstring and the docs must say it |
| An optional anyio import | Rejected by the author on 2026-10-22 (B-122): the shield would behave differently depending on what is installed |
| Accept and document the busy spin under anyio cancel scopes | Rejected by the author on 2026-10-22 (B-122): a cancelled dispatch would burn a CPU for the length of its commit |
| Store-side calls follow `uow.shield_hooks` | Rejected by the author on 2026-10-22 (B-123): the store and the unit of work are separate resources |
| An `IdempotencyBehavior(store, shield=False)` flag | Rejected by the author on 2026-10-22 (B-123): a store that works from any task needs no flag |
| Keep committing when the pipeline never reaches the handler | Rejected by the author on 2026-10-22 (B-116): an empty commit after a cached duplicate writes nothing useful, and a short-circuiting behavior's staged work would be committed without the handler |
| Leave a cancelled, committed command unrecorded | Rejected by the author on 2026-10-22 (B-109): its retry would run the handler again |
| Record a committed command only on a cancellation | Rejected by the author on 2026-10-22 (B-121): any other error after the commit would rerun it |
| Shield `_flush()` and `_write_outbox()` too | Rejected by the author on 2026-10-22 (B-110): they run before the point of no return, so a cancellation there rolls back with nothing lost |
| Allow a second `next()` as a retry in a new scope | Rejected by the author on 2026-10-22 (B-111): a scope commits once (ADR-068), and after a commit nothing is safe to repeat |
| Document "call `next()` at most once" only | Rejected by the author on 2026-10-22 (B-111): a persisted command would still be reported as failed with `UnitOfWorkAlreadyCommittedError` |
| A second list of behaviors between the handler and the commit | Rejected by the author on 2026-10-22 (B-112): two orders to teach for a case no documented behavior has |
| Bundle scope-keyed buffers (F-6) | Rejected by the author on 2026-10-22 (B-113): changes how every repository buffers events |
| Dispatch a committed, then cancelled command's events in-process under the shield | Rejected by the author on 2026-10-22 (B-124): delays timeouts and shutdown by every handler, and the events are still lost on a crash |
| Hand those events to a background task | Rejected by the author on 2026-10-22 (B-124): still lost on a crash |
| Ship it as a breaking change on `release/v0.5` | Rejected by the author on 2026-10-22 (B-114) |
| Allow anyio anywhere outside `ddd`, or drop anyio from the architecture rule | Rejected by the author on 2026-10-22 (B-128): the dependency is for one helper |
| Release the token when a post-commit `set` fails | Rejected by the author on 2026-10-22 (B-129): an immediate duplicate would run the committed command again |
| Keep raising when a post-commit `set` fails, and document it | Rejected by the author on 2026-10-22 (B-129): the caller would see a failure for a committed command |
| The helper raises `CommandExecutionError` directly for a hook's own `CancelledError` | Rejected by the author on 2026-10-22 (B-130): the unit of work does not know the command, and direct users would get a bus error |
| Keep the hook's raw `CancelledError` and drop B-126's wrapping | Rejected by the author on 2026-10-22 (B-130): the caller's task would look cancelled with nobody cancelling it |
| Add trio to the dev dependencies and drop support for hand-driven coroutines | Rejected by the author on 2026-10-22 (B-131): the B-69 tests and hand-driven use would break |
| Roll back when a cancellation is pending before `_commit()` | Rejected by the author on 2026-10-22 (B-132): `task.cancelling()` stays raised after code that catches a cancellation without `uncancel()`, so every later commit would roll back |
| Declare a high anyio floor now | Rejected by the author on 2026-10-22 (B-133): the floor is raised only if the tests at 4.0 fail |
| Leave `reserve()` unprotected and document it | Rejected by the author on 2026-10-22 (B-125): a cancellation while it runs would block retries until the TTL |
| Let a hook's own `CancelledError` through as a cancellation | Rejected by the author on 2026-10-22 (B-126): the caller's task would look cancelled with nobody cancelling it |
| `FakeUnitOfWork(fail_at=...)` fails every scope until reset; a `CQRSError` default | Rejected by the author on 2026-10-22 (B-127): fail-once matches `FakeOutboxStore.fail_next_append`, and a storage failure is not a library error |

Reordering the behaviors without moving the commit was not offered: it fixes neither CQRS-K1 nor CQRS-K3.

## Consequences

### Positive

- A failed or cancelled commit is never reported as a cached success: its retry runs the handler (CQRS-K1).
- Locked read-modify-write commands on one key no longer lose updates (CQRS-K3).
- With a store that implements `MessageReservationStore`, one `command_id` runs its handler at most once at a
  time, with or without lock keys (CQRS-04), and a late release never frees another dispatch's reservation.
- A rollback is not cut short by a second cancellation, and a commit that took effect is not rolled back after
  a cancellation, under asyncio, anyio and trio (CQRS-07). A committed command is never run again.
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
     nothing (B-116);
  5. a second `next()` in a command pipeline raises `PipelineReentryError`;
  6. `ctx.new_events` is set for behaviors;
  7. a cancellation during `_commit()` or `_rollback()` waits for it, a committed scope is no longer rolled
     back after a cancellation, and its events are not dispatched in-process (B-124).
- `anyio>=4` becomes a runtime dependency (B-122, B-133), imported only by `pydomain.cqrs.unit_of_work` (B-128);
  every page that lists the runtime dependencies changes.
- A post-commit `set` that fails is only logged; with a reserving store the id stays reserved until its TTL, and
  a retry after the TTL runs the handler again (B-129).
- At-least-once remains (point 3): a crash between the commit and the record, or an error after the storage
  commit took effect, runs the handler again on retry. Planned for 0.5.0 (W3-6).
- Under asyncio, `_commit()` and `_rollback()` run in their own task, in the caller's context. Storage keyed by
  the current task silently loses its writes there; such a unit of work must set `shield_hooks = False` and
  gives up the shield (B-115, B-119). ADR-076 kept lock releases in the acquiring task for a similar reason.
- A store must work from any task (B-123): a store built on the unit of work's task-scoped session is not
  supported.
- A cancelled dispatch can outlive its cancellation by the duration of the commit, the rollback or the
  post-commit record.
- A reservation left by a crashed process, or by a `release` that failed, blocks its `command_id` with
  `CommandInProgressError` until the adapter's TTL frees it.
- A committed command that is then cancelled, or fails after its commit, never reaches in-process event
  handlers, and its retry returns the cached result (B-124); only the outbox path delivers its events.

### Neutral

- Locks are held for the commit's duration as well as the handler's.
- The rollback and the exit drain still run after the locks are released, and sharing a repository between
  concurrent dispatches is still unsafe (B-19, F-6).
- A Protocol-only unit of work and a store without `MessageReservationStore` keep today's behaviour for
  shielding and duplicates.
- Constraints on other clusters: W2-2 can reuse `MessageReservationStore` for "mark only after success", and
  keeps the key spaces of point 4 separate without changing the reservation semantics; under W2-10 a tracing
  behavior at slot 0 spans the commit, and W2-10's channel must be readable in the terminal step; W3-1 decides
  how `CommandInProgressError` and `PipelineReentryError` are wrapped.

## References

- `design-docs/02-commit-in-pipeline.research.md`, `design-docs/02-commit-in-pipeline.alignment.md`,
  `design-docs/02-commit-in-pipeline.frame.md`
- [Decision log](../others/decision-log.md#12-library-behaviour), B-103 to B-133; B-19, F-6 and F-10
- `docs/others/bug-audit-findings.md`: CQRS-K1, CQRS-K3, CQRS-04, CQRS-07, CQRS-11, CQRS-21, FAKE-04
- `docs/others/bug-fix-plan.md` §9.1 (W2-1), §9.2, §10 (W3-6)
- arc42 [§9.30](../arch42/09-design-decisions.md#930-commit-inside-the-command-pipeline)
- [ADR-005](ADR-005-publish-after-commit.md), [ADR-046](ADR-046-event-handlers-fail-independently.md),
  [ADR-062](ADR-062-eventbus-application-layer-peer.md),
  [ADR-071](ADR-071-library-failure-paths-carry-no-payload-values.md),
  [ADR-076](ADR-076-release-every-lock-under-cancellation-and-failing-releases.md),
  [ADR-078](ADR-078-cleanup-errors-stored-versions-and-per-class-configure.md)
