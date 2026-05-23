# ADR-041: Optimistic Concurrency via `expected_version` + `command_id` Idempotency

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

In a concurrent environment, two commands may try to modify the same aggregate simultaneously. Without concurrency control, the second write silently overwrites the first (lost update).

Additionally, the same command may be submitted twice (network retry, user double-click). Without command-level deduplication, the same business action executes twice.

## Decision

Two complementary mechanisms in `EventStore.append_to_stream()`:

### 1. Optimistic Concurrency via `expected_version`

```python
async def append_to_stream(
    self,
    aggregate_id: str,
    events: Sequence[DomainEvent],
    expected_version: int,
    command_id: UUID | None = None,
) -> None: ...
```

- `expected_version` is the number of events currently in the stream.
- If the actual stream length differs, the event store raises `ConcurrencyError`.
- For a new stream, `expected_version == 0` — any existing events mean a conflict.

The repository computes `expected_version` from the aggregate's version:

```python
expected_version = aggregate.version - len(events)
```

### 2. Command-Level Idempotency via `command_id`

- `command_id` uniquely identifies the command that produced these events.
- If the same `command_id` was already processed for this aggregate, the store raises `DuplicateCommandError`.
- This prevents the same command from appending events twice (network retry, saga recovery).

Both checks happen atomically in `append_to_stream()` — no race condition between the version check and the command ID check.

### Exception Hierarchy

```python
class ConcurrencyError(DomainError): ...     # Version mismatch
class DuplicateCommandError(DomainError): ... # Duplicate command_id
```

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Pessimistic locking (lock aggregate before loading) | Reduces throughput; deadlock risk; not suitable for event-sourced systems |
| Last-write-wins (no concurrency control) | Lost updates; data corruption |
| Event-level deduplication (skip duplicate events) | Does not prevent the root cause (concurrent modification) |
| `command_id` only (no version check) | Does not prevent concurrent different commands on the same aggregate |

## Consequences

### Positive

- Optimistic concurrency is lock-free — no deadlock risk, high throughput under low contention.
- `expected_version == 0` for new streams prevents stream creation races.
- `command_id` deduplication handles network retries and saga recovery safely.
- Both checks are atomic — no partial state.

### Negative

- Under high contention, `ConcurrencyError` is common — callers must retry with fresh state.
- `command_id` is optional — callers must pass it to get idempotency guarantees.

### Neutral

- The `ConcurrencyError` exception is in `ddd/exceptions.py` (domain layer) because it represents a business constraint. `DuplicateCommandError` is in `es/exceptions.py` (ES layer).

## References

- `src/pydomain/es/event_store.py` — `EventStore.append_to_stream()` signature
- `src/pydomain/es/event_sourced_repository.py` — `save()` computes `expected_version` and passes `command_id`
- `src/pydomain/ddd/exceptions.py` — `ConcurrencyError`
- `src/pydomain/es/exceptions.py` — `DuplicateCommandError`
