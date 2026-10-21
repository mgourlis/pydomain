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

### `ProcessedCommandStore`

```python
from pydomain.cqrs.idempotency import ProcessedCommandStore, MISSING


class ProcessedCommandStore(Protocol):
    async def get(self, command_id: UUID) -> Any:
        """Return the cached result, or MISSING."""
        ...

    async def set(self, command_id: UUID, result: Any) -> None:
        """Persist result for command_id."""
        ...

    async def contains(self, command_id: UUID) -> bool:
        """Return True if command_id has been processed."""
        ...
```

The `MISSING` sentinel distinguishes "never processed" from a cached `None` result.

### How It Works

```
1. Command arrives with command_id=abc-123
2. IdempotencyBehavior checks store.get("abc-123")
3. If cached → return cached result (handler skipped)
4. If MISSING → call handler, store.set("abc-123", result)
```

## Distributed Locking

### The Problem

Two concurrent commands targeting the same aggregate can interleave, causing lost updates or invariant violations.

### The Solution

`AggregateLockingBehavior` acquires a lock keyed by aggregate ID before the handler runs, ensuring only one command modifies a given aggregate at a time.

Locking does not make a repository shared by concurrent units of work safe. The locks are per aggregate key, so commands on different aggregates still share the repository's event buffer, and they are released before the commit and the unit of work's exit (CQRS-K3, fixed by W2-1). Build the repository per unit of work when dispatches run concurrently ([Unit of Work](unit-of-work.md#rollback), ADR-068).

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

Two promises go with the signatures, and `LockProviderContract` checks both against every provider (ADR-076):

- An `acquire()` cancelled while it waits never holds the key.
- Locks are **not reentrant**: a second `acquire()` of a key from the task that holds it is never granted. A provider may raise `LockReentryError` or block.

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

- after the handler returns;
- after the handler or an `acquire()` raises;
- after a cancellation, including one that arrives while the behavior waits on its second key.

The releases run in one task that the behavior awaits through `asyncio.shield`. A cancellation that arrives while they run, such as a second `cancel()` or an anyio cancel scope, stops the wait, not the releases: the cancellation propagates at once and the releases finish on their own. Because of this, `release()` runs in a different task from `acquire()`, so a provider must not tie a lock to the task that acquired it.

Each `release()` is isolated, so one that fails does not skip the others. A failure is reported by its exception type only, never the lock key, which is built from the message:

| When | What you see |
|------|--------------|
| The handler succeeded and a release raised | The command still succeeds and commits. A WARNING on `pydomain.pipeline`: `Lock release failed for ReserveStock: ConnectionError` |
| The handler raised and a release raised too | The handler's error, with a note `clean-up also failed: ConnectionError` per failed release |
| A release raised a `BaseException` that is not an `Exception` (a cancellation) | That exception, once every other release ran |
| The caller was cancelled during the releases | A WARNING per failed release from the background task: `Abandoned lock release for ReserveStock failed with ConnectionError` |

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

In the recommended pipeline configuration, idempotency (slot 3) runs before locking (slot 4):

```
1. LoggingBehavior
2. ValidationBehavior
3. IdempotencyBehavior   ← duplicate check here
4. AggregateLockingBehavior  ← lock here (skipped if duplicate)
—  Terminal handler
```

This ordering avoids wasted lock acquisition on commands that will be skipped.

## Next Steps

- **[Add Idempotency →](../../how-to/cqrs/add-idempotency.md)** — step-by-step configuration
- **[Add Distributed Locking →](../../how-to/cqrs/add-distributed-locking.md)** — locking setup
- **[Pipeline Behaviors →](pipeline-behaviors.md)** — how behaviors compose
- **[Fake Processed Command Store →](../../how-to/testing/use-fake-processed-command-store.md)** — testing idempotency
