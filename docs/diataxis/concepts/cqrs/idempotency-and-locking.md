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

### LockKeyResolver

```python
from pydomain.cqrs.locking import LockKeyResolver


class LockKeyResolver(Protocol):
    def resolve(self, message: Any) -> list[str]:
        """Return lock keys for the message. Empty list = no locking."""
        ...
```

### Deadlock Prevention

Keys are acquired in **sorted** order and released in **reverse** order inside a `finally` block. If any key fails to acquire, already-held keys are released before raising.

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
