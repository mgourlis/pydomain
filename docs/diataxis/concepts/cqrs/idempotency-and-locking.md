# Idempotency & Locking

> **Adoption Level:** 3 — CQRS with At-Least-Once Delivery
> **Module:** `pydomain.cqrs.idempotency`, `pydomain.cqrs.locking`

## Overview

Two complementary mechanisms protect against duplicate processing and concurrent modification in distributed systems:

| Mechanism | Protects Against | Module |
|-----------|-----------------|--------|
| **Idempotency** | Duplicate commands (at-least-once delivery) | `idempotency` |
| **Distributed Locking** | Concurrent modification of the same aggregate | `locking` |

They work together: idempotency checks happen before locking, so duplicate commands skip lock acquisition entirely.

## Idempotency

### The Problem

In distributed systems, messages can be delivered more than once. Without idempotency, processing the same `PlaceOrder` command twice creates two orders.

### The Solution

The `IdempotencyBehavior` tracks which command IDs have been processed and caches their results. When a duplicate arrives, the cached result is returned immediately — the handler is never called.

### `ProcessedMessageStore`

```python
from pydomain.cqrs.idempotency import ProcessedMessageStore, MISSING


class ProcessedMessageStore(Protocol):
    async def get(self, message_id: UUID) -> Any:
        """Return the cached result, or MISSING."""
        ...

    async def set(self, message_id: UUID, result: Any) -> None:
        """Persist result for message_id."""
        ...

    async def contains(self, message_id: UUID) -> bool:
        """Return True if message_id has been processed."""
        ...

    async def check_and_set(self, message_id: UUID) -> bool:
        """Atomically mark message_id; True if it was already processed."""
        ...
```

The `MISSING` sentinel distinguishes "never processed" from a cached `None` result.

### How It Works

```
1. Command arrives with command_id=abc-123
2. IdempotencyBehavior checks store.get("abc-123")
3. If cached → return cached result (handler skipped, nothing committed)
4. If MISSING → next(): the handler runs and the unit of work commits
5. After the commit → store.set("abc-123", result)
```

The result is recorded only after the commit succeeded, so a command whose commit failed is not cached: a retry with the same `command_id` runs the handler again. If `store.set()` itself fails after the commit, the behavior logs a WARNING on `pydomain.pipeline` with the error's type only (`Idempotency record failed for PlaceOrder: ConnectionError`) and still returns the result; a retry then runs the handler again.

A command whose commit completed (with an `AbstractUnitOfWork`; a Protocol-only unit of work that raises from `commit()` counts as not committed) is recorded even when its dispatch then raises: a cancellation that arrived during the commit (the commit finishes first), during the record itself, or during a lock release after the commit, a handler that called `commit()` itself, and a behavior that calls `next()` a second time. The behavior records the result, then re-raises the error, so a retry returns the cached result instead of running the committed work again. The record after a commit is protected from cancellation the same way as the commit, so the store must work from any task: use a connection pool (`redis.asyncio`, an asyncpg pool, an `AsyncEngine` with a short session per call), never the unit of work's task-scoped session. A record that fails there becomes a note on the re-raised error, `clean-up also failed: <type>`, not a WARNING.

Such a command does not return its events, so `MessageBus` never hands them to in-process event handlers, and the retry returns the cached result without them. Projections that must see every committed change read them through the outbox path (the outbound relay, the inbound gateway, or a catch-up subscription), which also survives a crash; in-process handlers stay best-effort.

This is **at-least-once**, not exactly-once. A retry runs the handler again, although the work was committed, when no record was written:

- the process crashed between the commit and the record;
- `_commit()` raised after the storage commit took effect (an ambiguous commit failure, such as a lost connection after the database committed): the unit of work rolls back, the caller sees a failure, and the retry applies the work a second time;
- a handler that called `commit()` itself and then raised, so there is no result to record;
- the dispatch was closed while suspended, as when a pending task is garbage-collected: a closing coroutine cannot wait for the store.

Make the handler safe to repeat where that matters, for example with a unique constraint or an optimistic-concurrency check. Recording the result inside the same transaction as the work is planned for a later release.

## Distributed Locking

### The Problem

Two concurrent commands targeting the same aggregate can interleave, causing lost updates or invariant violations.

### The Solution

`AggregateLockingBehavior` acquires a lock keyed by aggregate ID before the handler runs, ensuring only one command modifies a given aggregate at a time.

The locks are held until the command has committed: `AggregateLockingBehavior` releases them after `next()`, which includes the commit. A second command on the same key therefore reads what the first one committed. Give a lock lease or TTL room for the commit as well as the handler.

Locking does not make a repository shared by concurrent units of work safe. The locks are per aggregate key, so commands on different aggregates still share the repository's event buffer, and the locks are released before the unit of work's exit drains that buffer (a known limit, planned for a later release). Build the repository per unit of work when dispatches run concurrently ([Unit of Work](unit-of-work.md#rollback), ADR-068).

### LockProvider

```python
from pydomain.cqrs.locking import LockProvider


class LockProvider(Protocol):
    async def acquire(self, key: str) -> None:
        """Acquire the lock, blocking until held."""
        ...

    async def release(self, key: str) -> None:
        """Release the lock."""
        ...
```

Three promises go with the signatures, and `LockProviderContract` checks each against every provider (ADR-076):

- An `acquire()` cancelled while it waits never holds the key.
- Locks are **not reentrant**: a second `acquire()` of a key from the task that holds it is never granted. A provider may raise (`FakeLockProvider` raises `LockReentryError`) or block, but must keep the first hold.
- `release()` is **ownership-safe**: a release of a hold that already ended never frees the lock another holder took since. It may do nothing or raise.

### LockKeyResolver

```python
from pydomain.cqrs.locking import LockKeyResolver


class LockKeyResolver(Protocol):
    def resolve(self, message: Any) -> list[str]:
        """Return lock keys for the message. Empty list = no locking."""
        ...
```

### Deadlock Prevention

Keys are acquired in **sorted** order (ADR-019), so two commands that need the same keys never wait on each other in opposite order.

### Release Guarantees

Every key whose `acquire()` returned is released, in **reverse** order, however the command ends (ADR-076):

- after the handler returns and the unit of work commits;
- after the handler, the commit or an `acquire()` raises;
- after a cancellation, including one that arrives while the behavior waits on its second key.

The releases run in the task that acquired the locks, so a provider whose locks belong to that task (an `anyio.Lock`, a client that checks the current task) works. If that task is cancelled while a release runs, for example by a second `cancel()` or an anyio cancel scope, the interrupted key and the keys after it move to one background task, and the cancellation propagates at once; the caller does not wait for that task. This holds whether the provider re-raises the cancellation, converts it into its own error or swallows it. A task-owned provider whose `release()` awaits is interrupted and refuses these background releases, so give it a lease or TTL for this case.

The background task calls the interrupted release **again**, because the interruption may have come after the release took effect. So `release()` must be **ownership-safe, not just idempotent**: if the key was freed and another command took it, the repeated call must not free that command's lock, or two commands hold the key at once. Tie each hold to its acquire with a token, as `FakeLockProvider` does and the [Redis example](../../how-to/cqrs/add-distributed-locking.md#production-lock-provider-redis-example) shows (ADR-076).

The cancellation handling needs asyncio. Under another anyio backend, such as trio, the behavior releases every key inline, with no hand-over, so a cancellation during the releases can interrupt them.

Each `release()` is isolated, so one that fails does not skip the others. A failure is reported by its exception type only, never the lock key, which is built from the message:

| When | What you see |
|------|--------------|
| The handler and the commit succeeded and a release raised | The command still succeeds: the commit has already happened. A WARNING on `pydomain.pipeline`: `Lock release failed for ReserveStock: ConnectionError` |
| The handler or the commit raised and a release raised too | That error, with a note `clean-up also failed: ConnectionError` per failed release |
| A release raised a `BaseException` that is not an `Exception` (a cancellation) | That exception, once every other release ran |
| The caller was cancelled during the releases | The `CancelledError`, with a note per release that had already failed; a WARNING per failed release from the background task: `Abandoned lock release for ReserveStock failed with ConnectionError` |

A lock whose release failed is the provider's to recover, typically through a lease or TTL.

### Reentrancy

Locks are **not reentrant**. A handler must not dispatch a nested command whose keys include one it already holds:

- with `FakeLockProvider`, the nested acquire raises `LockReentryError` (a `CQRSError`), which reaches the caller wrapped in `CommandExecutionError`;
- with another provider, it may block forever;
- a nested dispatch started in a **new task** (`asyncio.create_task`, a task group) is a different task, so it waits on the outer command's lock while the outer command waits on it, and deadlocks with every provider.

Dispatch the second command after the first returns, or give the commands different keys.

### `DictLockKeyResolver`

A registry-based resolver that maps message types to key-extraction functions:

```python
from pydomain.cqrs.locking import DictLockKeyResolver

resolver = DictLockKeyResolver()
resolver.register(PlaceOrder, lambda cmd: [f"order:{cmd.order_id}"])
resolver.register(CancelOrder, lambda cmd: [f"order:{cmd.order_id}"])
```

## Pipeline Slot Order

> **Known limit:** with idempotency before locking, two concurrent dispatches of the same `command_id` can both run the handler. This is a known limit, planned for a later release.

In the recommended pipeline configuration, idempotency (slot 3) runs before locking (slot 4):

```
1. LoggingBehavior
2. ValidationBehavior
3. IdempotencyBehavior   ← duplicate check here
4. AggregateLockingBehavior  ← lock here (skipped if duplicate)
—  Terminal step: handler, then commit
   then, unwinding: release the locks, record the result
```

This ordering avoids wasted lock acquisition on commands that will be skipped. Because the commit runs in the terminal step, the locks are released and the result is recorded only after the commit.

## Next Steps

- **[Add Idempotency →](../../how-to/cqrs/add-idempotency.md)** — step-by-step configuration
- **[Add Distributed Locking →](../../how-to/cqrs/add-distributed-locking.md)** — locking setup
- **[Pipeline Behaviors →](pipeline-behaviors.md)** — how behaviors compose
- **[Fake Processed Message Store →](../../how-to/testing/use-fake-processed-message-store.md)** — testing idempotency
