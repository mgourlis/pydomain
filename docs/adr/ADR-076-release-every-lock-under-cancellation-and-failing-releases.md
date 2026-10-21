# ADR-076: Release Every Lock Under Cancellation and Failing Releases

## Status

Proposed. Amends [ADR-019](ADR-019-sorted-lock-keys-deadlock-prevention.md).

## Date

2026-10-21

## Context

ADR-019 makes `AggregateLockingBehavior` acquire its lock keys in sorted order and release them in reverse, and
claims that "symmetric release order prevents lock leaks". The 0.3.0 audit (`docs/others/bug-audit-findings.md`,
`docs/others/bug-audit-plan.md` §12, rows CQRS-01 to CQRS-03) found that the code ADR-019 quotes leaks locks
in two ways, and that a third case is not specified:

1. **A cancelled acquire leaves the earlier locks held** (CQRS-01). The acquire loop releases what it holds in
   an `except Exception` block. `asyncio.CancelledError` is a `BaseException`, so a dispatch cancelled while it
   waits on its second key keeps the first one. Every later command on that key blocks forever.
2. **One failing release strands the rest** (CQRS-02). The `finally` block releases the keys one after another
   with no isolation, so a `release()` that raises (a lost connection to the lock server) skips every key after
   it. The exception also leaves the pipeline inside the command's unit of work, so the bus rolls back a command
   whose handler succeeded and raises `CommandExecutionError`.
3. **Reentrancy is unspecified** (CQRS-03). A handler that dispatches a nested command with the same lock key
   waits on a lock its own task holds. `FakeLockProvider` deadlocks, as its docstring warns; the `LockProvider`
   Protocol says nothing.

The releases also ran in the dispatching task. When that task is cancelled again while a release awaits the
lock server, or runs inside an anyio cancel scope that has been cancelled (anyio cancels every later await in
the scope), the release is interrupted and the key stays held.

`AggregateLockingBehavior` still does not protect a repository's event buffer shared by concurrent units of
work; that stays documented, not fixed (decision log B-19, ADR-068).

## Decision

We will release every lock the behavior acquired, whatever ends the command, and make non-reentrancy an
explicit, documented rule. This amends ADR-019 on its release guarantees; key sorting and deduplication are
unchanged. The author decided each point on 2026-10-21
([decision log](../others/decision-log.md#12-library-behaviour) B-48, B-50 to B-52).

1. **Every acquired key is released, whatever ends the command.** The behavior records each key once its
   `acquire()` returns. One block that covers the acquire loop and the handler releases the recorded keys in
   reverse order, after the handler returns or after any `BaseException` (a cancellation included) from an
   `acquire()` or the handler. A key whose `acquire()` did not return is not released.
2. **One failing release does not stop the others.** Each `release()` runs in its own `try` that catches
   `BaseException`, so every recorded key gets its `release()` call even when an earlier one raises.
3. **The releases are shielded from cancellation (B-51).** All the releases of one command run in one task,
   which the behavior awaits through `asyncio.shield`. A module-level set keeps a strong reference to the task
   until it finishes. A cancellation that arrives during the releases (a second `cancel()`, or an anyio cancel
   scope) stops the wait, not the releases: the cancellation propagates at once, and the task finishes on its
   own. Such an abandoned task logs each failed release as a WARNING on `pydomain.pipeline`, naming the
   exception's type only.
4. **How a failed release surfaces (B-50).** Any text names the exception's type only, never the lock key,
   which is built from the message (ADR-071):
   - **(a) The handler succeeded and a release raised an `Exception`:** the command still succeeds. The handler's
     result is returned, so the bus commits, and each failed release is logged as a WARNING on
     `pydomain.pipeline` naming the message type and `describe(exc)`, for example
     `Lock release failed for ReserveStock: ConnectionError`. A lock whose release failed is the lock
     provider's to recover (a distributed lock's lease or TTL).
   - **(b) The handler (or an `acquire()`) raised and a release raised too:** the original error propagates,
     with a note `clean-up also failed: <Type>` for each failed release, as the bus (ADR-068) and
     `bootstrap()` (ADR-073) do.
   - **(c) A release raised a `BaseException` that is not an `Exception`** (a cancellation, `KeyboardInterrupt`):
     the remaining keys are still released, then the first such `BaseException` is re-raised. It replaces the
     handler's result or error, and carries a note for each release that raised an `Exception`.
5. **Locks are not reentrant (B-48).** Acquiring a key the current task already holds is unsupported, for
   `AggregateLockingBehavior` and for every `LockProvider`: a handler must not dispatch a nested command that
   resolves to a lock key it holds.
   - `FakeLockProvider` raises `LockReentryError` when the task that holds a key acquires it again, instead of
     deadlocking (B-52). `LockReentryError` is a new `CQRSError` in `cqrs/exceptions.py`, exported from
     `pydomain.cqrs` and `pydomain`. Its message names no key: `FakeLockProvider: the current task already
     holds this lock key; locks are not reentrant (ADR-076)`.
   - `LockProviderContract` pins non-reentrancy in its loose form: a second `acquire()` of a held key from the
     same task is never granted. It may raise or block; the case waits a short time. A provider is not
     required to raise `LockReentryError`.
   - "Same task" is the asyncio task (`asyncio.current_task()`); `FakeLockProvider` runs on asyncio only. A
     nested dispatch spawned in a new task is a different task: it waits on the outer command's lock while
     the outer command waits on it, so it still deadlocks, with every provider.
6. **A cancelled `acquire()` never holds the key.** The `LockProvider` Protocol states it, and
   `LockProviderContract` pins it with `test_cancelled_acquire_does_not_take_the_key`: once the holder releases,
   the key is free.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Keep `except Exception` in the acquire loop and add `except asyncio.CancelledError` | Still misses `KeyboardInterrupt`, `SystemExit` and any other `BaseException`; one block covers every exit |
| Stop at the first failing release and re-raise it | Strands every later key until the provider's lease expires, and rolls back a command whose handler succeeded (CQRS-02) |
| Log a failed release after a successful handler at ERROR (B-50) | Rejected by the author on 2026-10-21 |
| Propagate a release's `BaseException` at once (B-50) | Rejected by the author on 2026-10-21: the remaining keys would stay held, which is CQRS-02 again |
| No shield, with the limit documented (B-51) | Rejected by the author on 2026-10-21: under an anyio cancel scope every release of a provider that awaits would be cancelled, and every key stranded |
| Shield each release separately (B-51) | Rejected by the author on 2026-10-21 |
| Make `AggregateLockingBehavior` reentrant by tracking held keys in a `ContextVar` (B-48) | Rejected by the author on 2026-10-21 |
| Document non-reentrancy only, keeping the fake's deadlock (B-48) | Rejected by the author on 2026-10-21: a test that nests a same-key dispatch hangs instead of failing with a message |
| `RuntimeError` for the fake's re-acquire (B-52) | Rejected by the author on 2026-10-21: it cannot be caught apart from other runtime errors |
| An error class defined only in `pydomain.testing` (B-52) | Rejected by the author on 2026-10-21 |
| A contract case that requires `LockReentryError` (B-52) | Rejected by the author on 2026-10-21: every adopter's provider would have to track which task holds each key |

## Consequences

### Positive

- A cancelled or failed command no longer leaves a lock held, so later commands on the same aggregate do not
  hang, even when the releases themselves are cancelled.
- A command whose handler succeeded is committed even when the lock server fails on release.
- A same-key nested dispatch fails fast in tests against `FakeLockProvider` with `LockReentryError`.

### Negative

- A failed release after a successful handler is only logged, so an adapter whose `release()` fails relies
  on its own lease or TTL to free the key.
- Code that caught a release error from `dispatch()` (as `CommandExecutionError`) no longer sees it.
- `release()` now runs in a task other than the one that called `acquire()`. A provider that ties a lock to
  the acquiring task (for example one built on `anyio.Lock`, or one checking `asyncio.current_task()` on
  release) refuses that release; such a provider must key ownership on something else, such as a token.
- After a second cancellation the behavior returns before its releases finish, so a caller that retries at
  once may wait briefly on its own previous lock.

### Neutral

- Locks are still released before the bus commits the unit of work; holding them through the commit is W2-1.
- `AggregateLockingBehavior` does not protect a shared repository's event buffer (B-19, ADR-068).
- `FakeLockProvider` still lets any task release a key (FAKE-07); only same-task re-acquire changes.

## References

- `src/pydomain/cqrs/behaviors.py`: `AggregateLockingBehavior`
- `src/pydomain/cqrs/locking.py`: the `LockProvider` Protocol
- `src/pydomain/cqrs/exceptions.py`: `LockReentryError`
- `src/pydomain/testing/fake_lock_provider.py`: `FakeLockProvider`
- `src/pydomain/testing/contracts/lock_provider.py`: `LockProviderContract`
- `tests/cqrs/test_locking_behavior_release.py`, `tests/testing/test_fake_lock_provider.py`
- `docs/others/bug-audit-findings.md`: CQRS-01, CQRS-02, CQRS-03
- `docs/others/bug-fix-plan.md` §8 (W1-8), §14 (D-CQRS-03)
- `docs/others/decision-log.md` §1.2: B-19, B-40, B-48, B-50, B-51, B-52
- ADR-004: Exception Hierarchy Organized by Layer
- ADR-019: Sorted Lock Keys for Deadlock Prevention (amended by this ADR)
- ADR-067: Protocol Contract Suites in `pydomain.testing.contracts`
- ADR-068: Rollback Drains Event Buffers
- ADR-071: Library-Owned Failure Paths Never Carry Payload Values
- ADR-073: Safe Bootstrap Wiring and the Application's Public Accessors
- YouTrack DCE-123
