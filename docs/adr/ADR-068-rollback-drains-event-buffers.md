# ADR-068: Rollback Drains Event Buffers and Is Idempotent

## Status

Proposed

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
bus's error contract.

1. **`rollback()` drains every repository's event buffer.** `AbstractUnitOfWork.rollback()` calls
   `pull_events()` on each repository in `_repos` and discards the result, then clears its collected events.
   `pull_events()` is already part of the `Repository` Protocol and is what `commit()` uses, so no new
   method is needed.
2. **`rollback()` is idempotent.** Calling it again in the same scope finds nothing to drain and changes
   nothing. Subclasses that override it to roll back a storage transaction should keep that property.
3. **`__aexit__` rolls back whenever the scope has not committed**, whether the body raised or not. A scope
   that committed is left as it is, also when the body raised after the commit.
4. **`collect_events()` returns a copy**, so a later scope on the same unit of work cannot change a list a
   caller already holds.
5. **`CommandBus.dispatch()` no longer calls `rollback()` itself.** It leaves the rollback to `__aexit__`,
   so a failed dispatch rolls back once. It wraps the failure in `CommandExecutionError` outside the
   `async with` block, with the handler's (or the commit's) exception as `__cause__`. If the rollback raises
   too, the caller still gets `CommandExecutionError` with the same `__cause__`; the rollback's exception is
   its `__context__`.
6. **A second `commit()` in one scope raises `UnitOfWorkAlreadyCommittedError`** (D-CQRS-10), a new
   `CQRSError`. It is raised at the start of `commit()`, before any hook, so nothing is flushed, written to
   the outbox or committed twice. This includes a retry after a failed `commit()`: the scope must roll back
   first. `__aenter__` and `rollback()` start a new scope. Inside a dispatch, a handler that commits (against
   ADR-020) makes the bus's own commit raise, and the caller gets `CommandExecutionError` with this error as
   its cause.
7. **`FakeUnitOfWork` resets its rollback record on entry**, so each scope reports only its own rollback.

`__aexit__` does not change what happens when the body raised and the rollback raises as well: the
rollback's exception propagates, with the body's exception as its `__context__`, as for any context manager
whose clean-up fails. The bus's wrapping (point 5) is what keeps `CommandExecutionError` for dispatches.

This restores the contracts of ADR-005, ADR-020 and the `UnitOfWork` Protocol; it does not change them.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| A new repository method to discard the buffer (`discard_events()`) | Adds a method to the `Repository` Protocol that every adopter's adapter must implement, for what `pull_events()` already does |
| Keep the bus's `rollback()` and make `rollback()` skip a second call with a flag | Subclasses override `rollback()` and roll back their transaction before calling `super()`; a flag in the base class does not stop their part running twice |
| Roll back in the bus and stop `__aexit__` rolling back on an exception | Breaks the Protocol's "exit without commit rolls back" for every direct user of a unit of work |
| `__aexit__` swallows a raising rollback and lets the body's exception propagate | Hides a storage failure from direct users; the bus can keep its error contract without it |
| Allow a second `commit()` and make it write only the new events | Two transactions in one scope with one set of tracing IDs; ADR-020 gives the commit to the bus, so a second commit is a handler bug that should be loud |
| Make the second `commit()` a no-op | Silently drops the events staged after the first commit |
| Clear the repositories' buffers on `__aenter__` instead of on rollback | Discards events of a repository shared with a unit of work that is still running; the failure that produced them is the place to discard them |

## Consequences

### Positive

- A refused command's events are never published, whether the repository lives in the unit of work or
  outlives it (ADR-005 holds for shared repositories).
- A failed dispatch rolls back exactly once, and the caller always gets `CommandExecutionError` with the
  original failure as its cause (ADR-020).
- A unit of work left without `commit()` rolls back, as the Protocol says.
- A dispatch's returned events stay what they were, whatever later scopes do.
- Committing twice by mistake fails loudly instead of duplicating outbox rows and transactions.

### Negative

- Behaviour changes that adopters may notice (listed in the upgrade notes): a clean exit without `commit()`
  now calls `rollback()`; a second `commit()` in one scope raises; `collect_events()` returns a new list on
  each call; the bus no longer calls `rollback()` before `__aexit__`.
- A subclass `rollback()` that is not idempotent, or that assumed it only runs after an exception, has to be
  checked.
- Draining on rollback depends on every repository in `_repos` having `pull_events()`, as `commit()` already
  does.

### Neutral

- `rollback()` after a successful `commit()` keeps its 0.3.0 behaviour: it clears the collected events and
  starts a new scope.
- A rollback still does not undo a save in `FakeRepository`; that is the repository version and copy rule of
  cluster W2-6 (FAKE-01, FAKE-13), not this ADR.
- `UnitOfWorkContract` (ADR-067) gains cases for draining on rollback, exit without commit, the returned
  copy and the second commit.

## References

- ADR-005: Publish Events After Commit, Never Before
- ADR-020: CommandBus Owns UoW Lifecycle — Handlers Never Call Commit/Rollback
- ADR-067: Protocol Contract Suites in `pydomain.testing.contracts`
- `src/pydomain/cqrs/unit_of_work.py`, `src/pydomain/cqrs/command_bus.py`,
  `src/pydomain/testing/fake_unit_of_work.py`, `src/pydomain/cqrs/exceptions.py`
- `docs/others/bug-audit-findings.md`: CQRS-K5, FAKE-03, ES-24, CQRS-06, CQRS-24, CQRS-08, FAKE-04, CQRS-10
- `docs/others/bug-fix-plan.md`: cluster W1-1, decision D-CQRS-10
- arc42 §5.3.7, §6.1, §8.3, §9.19
- YouTrack DCE-116
