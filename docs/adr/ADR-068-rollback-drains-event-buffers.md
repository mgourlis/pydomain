# ADR-068: Rollback Drains Event Buffers and Is Idempotent

## Status

Accepted. Amends [ADR-020](ADR-020-commandbus-owns-uow-lifecycle.md).

## Date

2026-10-21

## Context

ADR-005 promises that event handlers never see rolled-back state: events are published only after
`UnitOfWork.commit()` succeeds. ADR-020 gives the `CommandBus` the unit of work's lifecycle and says that
`CommandExecutionError` wraps the handler's failure. The `UnitOfWork` Protocol says that leaving the context
without `commit()` rolls back (`src/pydomain/cqrs/unit_of_work.py`). The 0.3.0 audit
(`docs/others/bug-audit-findings.md`) found that `AbstractUnitOfWork` and the bus keep none of these
promises when a repository outlives one unit of work, which the docs teach (`FakeUnitOfWork(repository=repo)`,
a shared `EventSourcedRepository` in `es-with-cqrs.md` and `all-modules.md`):

1. **A refused command's events reach the next commit** (CQRS-K5, FAKE-03, ES-24). `save()` moves an
   aggregate's events into the repository's buffer. `rollback()` cleared only the unit of work's own list,
   so the buffer survived, and the next `commit()` pulled the failed command's events and published them
   under the next command's tracing IDs.
2. **Rollback ran twice on every failure** (CQRS-06). The bus called `rollback()` in its `except` block, and
   `__aexit__` called it again. A rollback that raised replaced the `CommandExecutionError` with a raw
   storage error.
3. **A clean exit without `commit()` did not roll back** (CQRS-24), although the Protocol says it does.
4. **`collect_events()` returned the internal list** (CQRS-08, the aliasing part). The next `__aenter__`
   cleared it, so a caller holding a dispatch's events (as `dispatch_many` does during a nested dispatch on a
   shared unit of work) lost them.
5. **`FakeUnitOfWork` kept its rollback flag across scopes** (FAKE-04): `_rolled_back` was never reset.
6. **`commit()` could run twice in one scope** (CQRS-10, decision D-CQRS-10 in
   `docs/others/bug-fix-plan.md` §14). A second call flushed again, wrote every event collected so far to the
   outbox again and committed again: a probe recorded `outbox writes: [[1], [1, 2]]  db commits: 2`.

## Decision

We will make the unit of work's failure path discard everything the scope produced, run once, and keep the
bus's error contract. This amends ADR-020 on one point: the bus no longer calls `rollback()` itself.

1. **`rollback()` drains every repository's event buffer.** `AbstractUnitOfWork.rollback()` calls
   `pull_events()` on each repository in `_repos` and discards the result, then clears its collected events
   and starts a new scope. `pull_events()` is already part of the `Repository` Protocol and is what
   `commit()` uses, so no new repository method is needed. Every repository is drained even if one
   `pull_events()` raises; the first error is re-raised after the drain and the scope reset.
2. **Storage rollback goes in a new `_rollback()` hook.** `_rollback()` is an async no-op by default, like
   `_flush()` and `_commit()`. `rollback()` calls it first and runs the drain and the scope reset in
   `finally` blocks, so they run even when the storage rollback raises; the error is then re-raised.
   Subclasses override `_rollback()`, not `rollback()`.
3. **`rollback()` is idempotent for the events.** Calling it again in the same scope finds nothing to drain.
   `_rollback()` runs again, so it should be idempotent too: an explicit `rollback()` followed by leaving the
   block calls it twice, because the scope the explicit rollback started has not committed either.
4. **`__aexit__` rolls back whenever the scope has not committed**, whether the body raised or not. A scope
   that committed is not rolled back, also when the body raised after the commit, and keeps its collected
   events, but `__aexit__` still drains every repository's buffer: a change staged after the commit never
   reaches a later scope. There is no storage rollback for a committed scope.
5. **`collect_events()` returns a copy**, so a later scope on the same unit of work cannot change a list a
   caller already holds.
6. **`CommandBus.dispatch()` no longer calls `rollback()` itself.** It leaves the rollback to `__aexit__`,
   so a failed dispatch rolls back once. It wraps the failure in `CommandExecutionError` outside the
   `async with` block, with the handler's (or the commit's) exception as `__cause__`. If the rollback raises
   too, the caller still gets `CommandExecutionError` with the same `__cause__`; the rollback's exception is
   its `__context__`, and a note on the error names the rollback exception's **type** only
   (`clean-up also failed: ConnectionError`), never its message, which may carry content. A traceback shows
   the note. The same note appears when the drain of a committed scope raises after a handler's own
   commit, which is why it says "clean-up", not "rollback". If a `__aexit__` suppresses the failure, the
   bus still raises `CommandExecutionError`. An exception from the drain on exit of a dispatch that
   committed successfully propagates unwrapped, and no events are returned, though the commit persisted
   (decision-log B-20).
7. **A second `commit()` in one scope raises `UnitOfWorkAlreadyCommittedError`** (D-CQRS-10), a new
   `CQRSError`. It is raised at the start of `commit()`, before any hook, so nothing is flushed, written to
   the outbox or committed twice. The two cases it covers behave differently:
   - **After a successful first commit**, the first commit's changes stay persisted and the scope stays
     committed, so leaving it does not roll back. Inside a dispatch, a handler that commits (against
     ADR-020) makes the bus's own commit raise: the caller gets `CommandExecutionError` whose `__cause__` is
     `UnitOfWorkAlreadyCommittedError`, nothing is written twice, the handler's commit **is** persisted, and
     there is no rollback. Its events are never dispatched in-process, because the bus raises before it
     returns them; they reach consumers only through the outbox, if a writer is configured. Anything the
     handler staged after its commit is drained on exit (point 4). The error's message says so: "commit()
     was already called in this unit-of-work scope; changes from the first commit are persisted. Handlers
     must not call commit() (ADR-020)."
   - **After a first commit that failed part-way** (for example `_write_outbox()` or `_commit()` raised),
     a retry raises too, because the hooks that already ran would run again. The message says nothing from
     the failed commit is guaranteed persisted. `rollback()`, or entering the unit of work again, starts a
     new scope; the rollback drains the staged events, so the work has to be run again in that scope.
8. **`FakeUnitOfWork` resets its rollback record on entry**, so each scope reports only its own rollback.

When the body raised and the rollback raises as well, `__aexit__` keeps Python's default: the rollback's
exception propagates, with the body's exception as its `__context__`, as for any context manager whose
clean-up fails. A direct user of a unit of work therefore sees the rollback's exception and finds the original
failure in its `__context__`. The same default applies to a cancellation: `asyncio.CancelledError` passes
through `__aexit__`, which rolls back once, and the bus does not wrap it. The bus's wrapping (point 6) is what
keeps `CommandExecutionError`, with the original failure as `__cause__`, for dispatches.

A unit of work can be entered again after it exits, one scope at a time; the `UnitOfWork` Protocol says so,
and `UnitOfWorkContract` relies on it.

The author decided on 2026-10-21 the points this ADR first left open: `rollback()` after a successful commit
starts a new scope (see Neutral); a handler's own commit stays persisted and is not rolled back, but its
scope still drains on exit; the body-and-rollback case keeps Python's default; a retry after a failed commit
raises; storage rollback moves to the `_rollback()` hook; a rollback failure is noted by its type.

This restores the contracts of ADR-005 and the `UnitOfWork` Protocol, and amends ADR-020's description of
how the bus rolls back.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| A new repository method to discard the buffer (`discard_events()`) | Adds a method to the `Repository` Protocol that every adopter's adapter must implement, for what `pull_events()` already does |
| Keep the bus's `rollback()` and make `rollback()` skip a second call with a flag | Subclasses override `rollback()` and roll back their transaction before calling `super()`; a flag in the base class does not stop their part running twice |
| Keep overriding `rollback()` for storage, and document a `try`/`finally` around `super().rollback()` | Every adopter has to get the `finally` right; a storage rollback that raises before `super()` skips the drain and leaks the events this ADR exists to discard. The hook puts the `finally` in the library |
| Make `__aexit__` skip a scope that an explicit `rollback()` already rolled back | The scope that `rollback()` starts can stage new changes before the exit; `__aexit__` cannot tell, and skipping would leak them. `_rollback()` is required to be idempotent instead |
| Roll back in the bus and stop `__aexit__` rolling back on an exception | Breaks the Protocol's "exit without commit rolls back" for every direct user of a unit of work |
| `__aexit__` swallows a raising rollback and lets the body's exception propagate | Hides a storage failure from direct users; the bus can keep its error contract without it |
| Leave a rollback failure reachable only through `CommandExecutionError.__context__` | `raise … from` suppresses the context in tracebacks, so the rollback failure is invisible in logs; the note shows it. Its message could carry content, so the note names only the type |
| Allow a second `commit()` and make it write only the new events | Two transactions in one scope with one set of tracing IDs; ADR-020 gives the commit to the bus, so a second commit is a handler bug that should be loud |
| Make the second `commit()` a no-op | Silently drops the events staged after the first commit |
| Roll back when the bus's commit raises after a handler's own commit | The handler's commit is already durable and a rollback cannot undo it. Its events are not dispatched in-process either way, because the bus raises before it returns them; draining on exit already discards what the handler staged after its commit |
| Leave a committed scope's buffers alone on exit | A change staged after the commit, such as a handler's save after its own `commit()`, would reach the next scope's commit through a shared repository |
| Allow a retry after a failed `commit()` | The hooks that already ran (flush, outbox write) would run again and duplicate rows; a new scope after `rollback()` is the safe retry |
| Clear the repositories' buffers on `__aenter__` instead of on exit | Discards events of a repository shared with a unit of work that is still running; the exit of the scope that staged them is the place to discard them |
| Scope-keyed event buffers now (each unit of work gets its own buffer in a shared repository) | Rejected by the author on 2026-10-21 for this release: it changes how every `Repository` stores pending events and pairs with W2-1 (locks held through the commit). Deferred to backlog Feature F-6; until then a repository is shared only between units of work that run one after another |
| No drain on committed exit | Rejected by the author on 2026-10-21: it undoes the committed-exit drain and reopens the leak of changes staged after a commit |
| Wrap a drain error after a successful commit in `CommandExecutionError` | Rejected by the author on 2026-10-21: the commit persisted, so wrapping would report a successful command as failed; the error propagates unwrapped and is documented |

## Consequences

### Positive

- A refused command's events are never published, and neither is a change staged after a commit, whether
  the repository lives in the unit of work or outlives it (ADR-005 holds for shared repositories).
- A failed dispatch rolls back exactly once, and the caller always gets `CommandExecutionError` with the
  original failure as its cause and, if the rollback failed too, a note naming its type (ADR-020).
- The drain and the scope reset run even when the storage rollback or a repository raises.
- A unit of work left without `commit()` rolls back, as the Protocol says.
- A dispatch's returned events stay what they were, whatever later scopes do.
- Committing twice by mistake fails loudly instead of duplicating outbox rows and transactions.

### Negative

- Behaviour changes that adopters may notice (listed in the upgrade notes): a clean exit without `commit()`
  now calls `rollback()`; a committed scope drains its repositories on exit; a second `commit()` in one scope
  raises; `collect_events()` returns a new list on each call; the bus no longer calls `rollback()` before
  `__aexit__`.
- A subclass that rolled back storage by overriding `rollback()` should move that code to `_rollback()`. The
  storage rollback must be idempotent: an explicit `rollback()` followed by the exit runs it twice.
- Draining on exit depends on every repository in `_repos` having `pull_events()`, as `commit()` already
  does.
- **Sharing one repository is safe only between units of work that run one after another** (decision-log
  B-19). With concurrent dispatches on one shared repository, a scope's drain or commit can take another
  dispatch's buffered events: they are dropped, or published under the wrong command. Build the repository per
  unit of work when dispatches run concurrently. `AggregateLockingBehavior` does not make sharing safe: its
  locks are per aggregate key, so commands on different aggregates still share the buffer, and it releases
  them before the commit and the exit (CQRS-K3, fixed by W2-1). Per-scope buffers in a shared repository are
  backlog Feature F-6.

### Neutral

- `rollback()` after a successful `commit()` drains the repositories (only what was staged after the commit
  is left to drain), clears the collected events and starts a new scope, so a later `commit()` is allowed.
  It also calls `_rollback()`, which cannot undo the committed transaction.
- A rollback still does not undo a save in `FakeRepository`; that is the repository version and copy rule of
  cluster W2-6 (FAKE-01, FAKE-13), not this ADR.
- `UnitOfWorkContract` (ADR-067) gains cases for draining on rollback, exit without commit, a change staged
  after commit, the returned copy and the second commit.

## References

- ADR-005: Publish Events After Commit, Never Before
- ADR-020: CommandBus Owns UoW Lifecycle — Handlers Never Call Commit/Rollback (amended by this ADR)
- ADR-067: Protocol Contract Suites in `pydomain.testing.contracts`
- `src/pydomain/cqrs/unit_of_work.py`, `src/pydomain/cqrs/command_bus.py`,
  `src/pydomain/testing/fake_unit_of_work.py`, `src/pydomain/cqrs/exceptions.py`
- `docs/others/bug-audit-findings.md`: CQRS-K5, FAKE-03, ES-24, CQRS-06, CQRS-24, CQRS-08, FAKE-04, CQRS-10
- `docs/others/bug-fix-plan.md`: cluster W1-1, decision D-CQRS-10
- `docs/others/decision-log.md`: B-1 to B-5, B-14 to B-16, B-19, B-20; Feature F-6
- arc42 §5.3.7, §6.1, §8.3, §9.20, §11.8
- YouTrack DCE-116
