# ADR-018: MISSING Sentinel for Idempotency — Distinguishing "Never Processed" from Cached `None`

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

The `IdempotencyBehavior` caches command results in a `ProcessedCommandStore` to avoid re-executing duplicate commands. The store's `get(command_id)` method must distinguish between three states:

1. **Never processed**: The command ID is not in the store.
2. **Processed, returned a result**: The command was executed and produced a result.
3. **Processed, returned `None`**: The command was executed but the handler returned `None` or `EmptyCommandResult`.

Using `None` as the "not found" marker conflates cases 1 and 3 — if the store returns `None`, the behavior cannot tell whether the command was never processed or was processed with a `None` result.

## Decision

Use a unique sentinel object as the "not found" marker:

```python
MISSING: Any = object()
"""Sentinel returned by ProcessedCommandStore when no cached result exists."""

@runtime_checkable
class ProcessedCommandStore(Protocol):
    async def get(self, command_id: UUID) -> Any:
        """Return the cached result, or MISSING."""
        ...

    async def set(self, command_id: UUID, result: Any) -> None:
        """Persist result for command_id."""
        ...

    async def contains(self, command_id: UUID) -> bool:
        """Return True if command_id has already been processed."""
        ...
```

`IdempotencyBehavior` checks against `MISSING`:

```python
cached = await self._store.get(command_id)
if cached is not MISSING:
    return cached  # Return cached result (could be None, EmptyCommandResult, etc.)
```

Using `object()` ensures the sentinel is:
- **Identity-comparable**: `is MISSING` is always correct (no `__eq__` tricks).
- **Unique**: No other value in the system can be `MISSING`.
- **Unpicklable**: Cannot accidentally serialize the sentinel.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| `None` as "not found" | Cannot distinguish "never processed" from "processed with None result" |
| `Optional[Any]` return type (`result | None`) | Same conflating problem — `None` means both "not found" and "result is None" |
| `Result` monad with `.is_some()` / `.is_none()` | Over-engineered for a cache lookup; adds complexity |
| Raise `KeyError` for "not found" | Requires try/except on every lookup; exception flow for expected case is non-idiomatic |

## Consequences

### Positive

- Clear semantics: `is MISSING` unambiguously means "never processed".
- `None` can be legitimately cached as a command result.
- Simple implementation — a single `object()` sentinel.
- `contains()` method provides a boolean check without retrieving the result.

### Negative

- Callers must use `is` comparison (not `==`) — `MISSING` is identity-based.

### Neutral

- The sentinel is module-level and re-exported — users who implement `ProcessedCommandStore` must return `MISSING` for not-found cases.

## References

- `src/pydomain/cqrs/idempotency.py` — `MISSING` sentinel, `ProcessedCommandStore` Protocol
- `src/pydomain/cqrs/behaviors.py` — `IdempotencyBehavior` checks `is not MISSING`
