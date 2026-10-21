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

`AggregateLockingBehavior` still does not protect a repository's event buffer shared by concurrent units of
work; that stays documented, not fixed (decision log B-19, ADR-068).

## Decision

We will release every lock the behavior acquired, whatever ends the command, and make non-reentrancy an
explicit, documented rule. This amends ADR-019 on its release guarantees; key sorting and deduplication are
unchanged.

1. **Every acquired key is released in `finally`, one by one.** The behavior records each key once its
   `acquire()` returns, and a single `finally` block, which covers the acquire loop and the handler, releases
   the recorded keys in reverse order. A cancellation or any other `BaseException` during an `acquire()`
   releases the keys already held, then propagates. A key whose `acquire()` did not return is not released.
2. **One failing release does not stop the others.** Each `release()` runs in its own `try` that catches
   `BaseException`, so every recorded key gets its `release()` call even when an earlier one raises.
3. **A failed release after a successful handler does not fail the command.** The handler's result is
   returned, so the bus commits the unit of work. A lock whose release failed is the lock provider's to
   recover (a distributed lock's lease or TTL).
4. **Locks are not reentrant (B-48).** Acquiring a key the current task already holds is unsupported, for
   `AggregateLockingBehavior` and for every `LockProvider`: a handler must not dispatch a nested command that
   resolves to a lock key it holds. `FakeLockProvider` raises a clear error when the same task acquires a key it
   holds, instead of deadlocking, and `LockProviderContract` pins non-reentrancy. The author decided this on
   2026-10-21 (D-CQRS-03; [decision log](../others/decision-log.md#12-library-behaviour) B-48).
5. **A cancelled `acquire()` never holds the key.** The `LockProvider` Protocol states it, and
   `LockProviderContract` pins it with `test_cancelled_acquire_does_not_take_the_key`: once the holder releases,
   the key is free.

### Open points (awaiting the author)

- How a failed release surfaces: after a successful handler (a log line, its level and logger), after a failed
  handler (a type-only `clean-up also failed: <type>` note on the handler's error, as ADR-068 and ADR-073 do),
  and when the release raises a `BaseException` such as a cancellation (re-raised after the other releases, or
  swallowed). Any log or note names the exception's type only (ADR-071).
- Whether the releases are shielded from a cancellation that arrives while they run.
- The exception type and message `FakeLockProvider` raises on a same-task re-acquire, and whether
  `LockProviderContract` pins that error or only that the second acquire is not granted.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Keep `except Exception` in the acquire loop and add `except asyncio.CancelledError` | Still misses `KeyboardInterrupt`, `SystemExit` and any other `BaseException`; one `finally` covers every exit |
| Stop at the first failing release and re-raise it | Strands every later key until the provider's lease expires, and rolls back a command whose handler succeeded (CQRS-02) |
| Make `AggregateLockingBehavior` reentrant by tracking held keys in a `ContextVar` (D-CQRS-03) | Rejected by the author on 2026-10-21 (B-48): a nested dispatch would run inside the outer command's lock without its own isolation, and a provider shared by several behaviors or processes could not see the tracking |
| Document non-reentrancy only, keeping the fake's deadlock (D-CQRS-03) | Rejected by the author on 2026-10-21 (B-48): a test that nests a same-key dispatch hangs instead of failing with a message |

## Consequences

### Positive

- A cancelled or failed command no longer leaves a lock held, so later commands on the same aggregate do not
  hang.
- A command whose handler succeeded is committed even when the lock server fails on release.
- A same-key nested dispatch fails fast in tests against `FakeLockProvider` with a clear error.

### Negative

- A failed release is no longer raised, so an adapter whose `release()` fails silently relies on its own lease
  or TTL to free the key.
- Code that caught a release error from `dispatch()` (as `CommandExecutionError`) no longer sees it.

### Neutral

- Locks are still released before the bus commits the unit of work; holding them through the commit is W2-1.
- `AggregateLockingBehavior` does not protect a shared repository's event buffer (B-19, ADR-068).

## References

- `src/pydomain/cqrs/behaviors.py`: `AggregateLockingBehavior`
- `src/pydomain/cqrs/locking.py`: the `LockProvider` Protocol
- `src/pydomain/testing/fake_lock_provider.py`: `FakeLockProvider`
- `src/pydomain/testing/contracts/lock_provider.py`: `LockProviderContract`
- `tests/cqrs/test_locking_behavior_release.py`: the regression and failure-injection tests
- `docs/others/bug-audit-findings.md`: CQRS-01, CQRS-02, CQRS-03
- `docs/others/bug-fix-plan.md` §8 (W1-8), §14 (D-CQRS-03)
- `docs/others/decision-log.md` §1.2: B-19, B-48
- ADR-019: Sorted Lock Keys for Deadlock Prevention (amended by this ADR)
- ADR-067: Protocol Contract Suites in `pydomain.testing.contracts`
- ADR-068: Rollback Drains Event Buffers
- ADR-071: Library-Owned Failure Paths Never Carry Payload Values
- YouTrack DCE-123
