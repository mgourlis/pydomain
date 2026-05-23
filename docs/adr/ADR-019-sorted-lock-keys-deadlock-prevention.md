# ADR-019: Sorted Lock Keys for Deadlock Prevention

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

When multiple commands run concurrently and each acquires locks on aggregate instances, deadlock can occur if two commands acquire the same locks in different order:

```
Command A: lock(aggregate_1) → lock(aggregate_2)
Command B: lock(aggregate_2) → lock(aggregate_1)
→ Deadlock: each holds one lock and waits for the other
```

This is the classic AB/BA deadlock pattern. It can happen any time a command touches multiple aggregates and the lock acquisition order is non-deterministic.

## Decision

Lock keys are **sorted** before acquisition and **deduplicated** via `dict.fromkeys()`:

```python
class AggregateLockingBehavior:
    async def handle(self, ctx, next):
        keys = list(dict.fromkeys(sorted(self._resolver.resolve(ctx.message))))
        if not keys:
            return await next()

        acquired = []
        try:
            for key in keys:
                await self._provider.acquire(key)
                acquired.append(key)
        except Exception:
            for key in reversed(acquired):
                await self._provider.release(key)
            raise

        try:
            return await next()
        finally:
            for key in reversed(keys):
                await self._provider.release(key)
```

- **`sorted()`** ensures deterministic lock acquisition order across all commands.
- **`dict.fromkeys()`** removes duplicates while preserving order (a command that touches the same aggregate twice should only acquire one lock).
- **`reversed()`** on release ensures symmetric unlock order (innermost lock released first).

`DictLockKeyResolver` provides a registry-based approach:

```python
class DictLockKeyResolver:
    def register(self, message_type, key_fn): ...
    def resolve(self, message) -> list[str]: ...
```

Maps message types to key-extraction functions. Multiple functions per type are supported — keys are collected and sorted.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Unsorted lock acquisition | AB/BA deadlock possible; non-deterministic failure mode |
| Global lock (one lock for all commands) | Serialises all command execution; destroys concurrency |
| Lock timeout with retry | Adds complexity; does not prevent deadlock, only recovers from it |
| TryLock with backoff | Non-deterministic; may starve under high contention |

## Consequences

### Positive

- Deterministic lock order prevents AB/BA deadlocks by construction.
- Deduplication prevents self-deadlock (same aggregate touched twice).
- Symmetric release order prevents lock leaks.
- Registry-based resolver is extensible — new command types add resolvers without modifying the behavior.

### Negative

- Sort order depends on string comparison of lock keys — keys must be consistently formatted.
- Sorted acquisition is a convention, not enforced by the type system.

### Neutral

- `AggregateLockingBehavior` is a pipeline behavior (ADR-017) — it can be omitted for commands that don't need locking.

## References

- `src/pydomain/cqrs/behaviors.py` — `AggregateLockingBehavior`
- `src/pydomain/cqrs/locking.py` — `LockProvider`, `LockKeyResolver`, `DictLockKeyResolver`
