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
([decision log](../others/decision-log.md#12-library-behaviour) B-48, B-50 to B-52, B-68, B-69 and B-81).

1. **Every acquired key is released, whatever ends the command.** The behavior records each key once its
   `acquire()` returns. One block that covers the acquire loop and the handler releases the recorded keys in
   reverse order, after the handler returns or after any `BaseException` (a cancellation included) from an
   `acquire()` or the handler. A key whose `acquire()` did not return is not released.
2. **One failing release does not stop the others.** Each `release()` runs in its own `try` that catches
   `BaseException`, so every recorded key gets its `release()` call even when an earlier one raises.
3. **The releases run in the acquiring task, and move to a background task only on cancellation (B-51).**
   The behavior awaits each `release()` inline, in the task that acquired the locks, so a provider whose
   locks belong to that task (an `anyio.Lock`, a client that checks the current task) works. When a
   cancellation of that task arrives while a release runs (a second `cancel()`, or an anyio cancel scope,
   which cancels every later await in the scope), the interrupted key and the keys after it move to one
   background task that the cancellation cannot reach, and the caller propagates the cancellation at once,
   without waiting for that task. The behavior reads `asyncio.Task.cancelling()` before and after each
   release, so it sees the cancellation whether the provider re-raises it, converts it into its own error or
   swallows it (for a swallowed one, the release returned, so only the keys after it move). A
   `CancelledError` from a release while a cancellation of the task is pending, including one requested
   before the releases began, counts as that cancellation; one while nothing cancels the task is that
   release's failure (decision 4 (c)). The caller raises its own `CancelledError` (a new one when the
   provider converted or swallowed it), with a note for each release that failed inline before it. A
   module-level mapping keeps a strong reference to the background task until it ends. The task logs each
   failed release as a WARNING on `pydomain.pipeline`, naming the exception's type only, and logs
   `CancelledError` when it is cancelled at shutdown, even before its first step.
   - **The interrupted key is retried.** The background task calls `release()` again for the key whose
     release was interrupted, because the interruption may have come before or after the release took
     effect. A provider's `release()` must therefore be **ownership-safe, not just idempotent**: if the
     first call already freed the key and another command acquired it, the retry must not free that
     command's lock. A provider whose `release(key)` deletes whatever lock the key has (a plain `DEL` on
     Redis) would let a third command in while the second still holds the key: two holders at once. The
     `LockProvider.release` docstring states the rule, and decision 7 says how the fake keeps it.
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
     handler's result or error, and carries a note for each of the other failed releases.
5. **Locks are not reentrant (B-48).** Acquiring a key the current task already holds is unsupported, for
   `AggregateLockingBehavior` and for every `LockProvider`: a handler must not dispatch a nested command that
   resolves to a lock key it holds.
   - `FakeLockProvider` raises `LockReentryError` when the task that holds a key acquires it again, instead of
     deadlocking (B-52). `LockReentryError` is a new `CQRSError` in `cqrs/exceptions.py`, exported from
     `pydomain.cqrs` and `pydomain`. Its message names no key: `FakeLockProvider: the current task already
     holds this lock key; locks are not reentrant (ADR-076)`.
   - `LockProviderContract` pins non-reentrancy in its loose form: a second `acquire()` of a held key from the
     same task is never granted. It may raise any exception or block; the case waits a short time. Afterwards
     the first hold must still be held (a contender task has to wait), so a provider that refuses the second
     acquire by losing or corrupting the first hold fails. A provider is not required to raise
     `LockReentryError`.
   - "Same task" is the asyncio task (`asyncio.current_task()`); `FakeLockProvider` runs on asyncio only. A
     nested dispatch spawned in a new task is a different task: it waits on the outer command's lock while
     the outer command waits on it, so it still deadlocks, with every provider.
6. **A cancelled `acquire()` never holds the key.** The `LockProvider` Protocol states it, and
   `LockProviderContract` pins it with `test_cancelled_acquire_does_not_take_the_key`: once the holder releases,
   the key is free.
7. **`FakeLockProvider` releases are ownership-safe (B-68).** Each `acquire()` takes a new hold token and
   stores it in the calling context, in a `contextvars.ContextVar`. A task started from that context while it
   holds the key, such as the behavior's background task, sees the token. `release(key)` frees the key only
   when the calling context's token matches the current hold. A stale token, whose hold already ended, makes
   the release do nothing, so a late release never frees the lock another holder took. A context that holds
   no token for the key (a task started before the acquire, or one that never acquired) gets a
   `RuntimeError`, as `asyncio.Lock`, `threading.Lock` and `anyio.Lock` raise for releasing a lock the caller
   does not hold; the message names no key (amended B-68). A matching release drops its token from the
   calling context, so the context's tokens stay bounded however many keys a task uses, and a second release
   of the same hold in that context finds no token and raises `RuntimeError` too (B-81). A context copied
   before the release keeps the token, stale: the behavior's background task, when the interrupted release
   had not yet taken effect, holds the live token and releases; when it had, the copy was taken after the
   token was dropped, so the retry is refused with `RuntimeError`, which the background task logs by type.
   Either way the next holder keeps the key. The fake's `KeyError` for a key it never saw names no key
   either (ADR-071). The `release(key)` signature stays: the token travels
   in the context, not as an argument. A real provider does the same with a token stored on the lock server,
   for example `SET key token NX PX ttl` on Redis, and a compare-and-delete (a Lua script that deletes the
   key only while it still holds the token). `LockProviderContract` gains
   `test_late_release_after_another_holder_took_the_key_does_not_free_it`: the late release may do nothing
   or raise, but the second holder keeps the key.
8. **Without asyncio, the releases run inline (B-69).** Cancellation detection and the hand-over need an
   asyncio task. When none is running (anyio's trio backend, or a coroutine driven by hand) the behavior
   releases every key inline, in order, with no hand-over, and a cancellation exception from a release is
   that release's failure. The release guarantees under cancellation of decision 3 hold on asyncio only.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Keep `except Exception` in the acquire loop and add `except asyncio.CancelledError` | Still misses `KeyboardInterrupt`, `SystemExit` and any other `BaseException`; one block covers every exit |
| Stop at the first failing release and re-raise it | Strands every later key until the provider's lease expires, and rolls back a command whose handler succeeded (CQRS-02) |
| Log a failed release after a successful handler at ERROR (B-50) | Rejected by the author on 2026-10-21 |
| Propagate a release's `BaseException` at once (B-50) | Rejected by the author on 2026-10-21: the remaining keys would stay held, which is CQRS-02 again |
| Always release in one separate task, awaited through `asyncio.shield` (the first version of B-51) | Rejected by the author on 2026-10-21, after review: `release()` would never run in the acquiring task, so a provider whose locks belong to that task (an `anyio.Lock`, a client that checks the current task) refuses every release |
| Release inline without handing over on cancellation (B-51) | Rejected by the author on 2026-10-21: under an anyio cancel scope every release of a provider that awaits would be cancelled, and every key stranded |
| Shield each release separately (offered with the first answer to B-51) | Rejected by the author on 2026-10-21 |
| Make `AggregateLockingBehavior` reentrant by tracking held keys in a `ContextVar` (B-48) | Rejected by the author on 2026-10-21 |
| Document non-reentrancy only, keeping the fake's deadlock (B-48) | Rejected by the author on 2026-10-21: a test that nests a same-key dispatch hangs instead of failing with a message |
| `RuntimeError` for the fake's re-acquire (B-52) | Rejected by the author on 2026-10-21: it cannot be caught apart from other runtime errors |
| An error class defined only in `pydomain.testing` (B-52) | Rejected by the author on 2026-10-21 |
| Document the retry hazard only, leaving `FakeLockProvider`'s release unconditional (B-68) | Rejected by the author on 2026-10-21 |
| A silent no-op for a fake release from a context with no token (the first answer to B-68) | Rejected by the author on 2026-10-21 |
| Drop the retry of the interrupted key (B-68) | Rejected by the author on 2026-10-21 |
| Document that the behavior works on asyncio only (B-69) | Rejected by the author on 2026-10-21 |
| Add trio as a dev dependency to test B-69 under anyio's trio backend | Rejected by the author on 2026-10-21 |
| A contract case that requires every provider to detect reentry and raise `LockReentryError` (B-52) | Rejected by the author on 2026-10-21: every adopter's provider would have to track which task holds each key |
| A reentry case that accepts only `LockReentryError` from the second acquire (B-52) | Rejected by the author on 2026-10-21 |
| Dropping the token on release but making a second release a no-op (B-81) | Rejected by the author on 2026-10-21 |

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
- After a cancellation that arrives during the releases, the remaining releases run in a background task. A
  task-owned provider whose `release()` awaits (an `anyio.Lock` behind a network call, or a client that
  checks `asyncio.current_task()`) is interrupted there and refuses those background releases, and the keys
  stay held until its own lease or TTL frees them; each refusal is logged by type. Only this path is
  affected: every other release runs in the acquiring task, and a release that does not suspend completes
  inline.
- The interrupted release is called a second time from the background task. A provider whose `release()`
  is only idempotent (deletes whatever lock the key has) can then free a lock another command took in
  between, and two commands hold the key at once. Every provider must make `release()` ownership-safe
  (decision 7); the Redis how-to shows a token with a compare-and-delete.
- After such a cancellation the caller returns before the background releases finish, so a caller that
  retries at once may wait briefly on its own previous lock, and nothing it awaits reports their outcome
  except the WARNING lines.
- A loop closed without cancelling its tasks (`loop.close()` after `run_until_complete`, not `asyncio.run`)
  leaves a waiting background task, and its entry in the module's mapping, referenced until the process
  ends.
- On a backend other than asyncio a cancellation during the releases interrupts them (decision 8).

### Neutral

- Locks are still released before the bus commits the unit of work; holding them through the commit is W2-1.
- `AggregateLockingBehavior` does not protect a shared repository's event buffer (B-19, ADR-068).
- `FakeLockProvider` lets a release through only from the context that acquired, or a task started from it
  while it held the key. A stale token does nothing; a context without a token raises `RuntimeError` (B-68).
  This also narrows FAKE-07 (any task could release a key).

## References

- `src/pydomain/cqrs/behaviors.py`: `AggregateLockingBehavior`
- `src/pydomain/cqrs/locking.py`: the `LockProvider` Protocol
- `src/pydomain/cqrs/exceptions.py`: `LockReentryError`
- `src/pydomain/testing/fake_lock_provider.py`: `FakeLockProvider`
- `src/pydomain/testing/contracts/lock_provider.py`: `LockProviderContract`
- `tests/cqrs/test_locking_behavior_release.py`, `tests/testing/test_fake_lock_provider.py`
- `docs/others/bug-audit-findings.md`: CQRS-01, CQRS-02, CQRS-03
- `docs/others/bug-fix-plan.md` §8 (W1-8), §14 (D-CQRS-03)
- `docs/others/decision-log.md` §1.2: B-19, B-40, B-48, B-50, B-51, B-52, B-68, B-69, B-81
- ADR-004: Exception Hierarchy Organized by Layer
- ADR-019: Sorted Lock Keys for Deadlock Prevention (amended by this ADR)
- ADR-067: Protocol Contract Suites in `pydomain.testing.contracts`
- ADR-068: Rollback Drains Event Buffers
- ADR-071: Library-Owned Failure Paths Never Carry Payload Values
- ADR-073: Safe Bootstrap Wiring and the Application's Public Accessors
- YouTrack DCE-123
