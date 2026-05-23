# Saga Repository

> **Adoption Level:** 5 — Sagas & Process Managers
> **Module:** `pydomain.cqrs.saga.repository`
> **Prerequisites:** [Saga State](saga-state.md), [Repositories](../ddd/repositories.md)

## What is the SagaRepository?

The `SagaRepository` is a `Protocol` that defines the persistence contract for `SagaState`. It extends the standard repository concept with saga-specific queries for correlation-id lookup, stalled-saga recovery, and suspended-saga timeout handling.

```python
from pydomain.cqrs.saga.repository import SagaRepository
```

## Protocol methods

### `save(state)` — persist saga state

```python
async def save(self, state: SagaState) -> None:
    """Persist a SagaState (insert or update).

    Drains pending domain events from the state and stores them
    in an internal buffer for later retrieval via pull_events().

    Raises:
        ConcurrencyError: when the expected version does not match.
    """
```

Upserts the saga state. The implementation must handle optimistic concurrency via `state.version` — if the stored version doesn't match, raise `ConcurrencyError`.

### `get_by_id(id_)` — retrieve by identity

```python
async def get_by_id(self, id_: UUID) -> SagaState | None:
```

Standard aggregate lookup by primary key.

### `find_by_correlation_id(correlation_id, saga_type)` — correlation lookup

```python
async def find_by_correlation_id(
    self, correlation_id: UUID, saga_type: str
) -> SagaState | None:
```

The primary lookup used by `SagaManager` to find an existing saga instance for a given correlation chain. Returns `None` if no saga exists, prompting the manager to create a new one.

### `find_stalled_sagas(limit)` — crash recovery

```python
async def find_stalled_sagas(self, limit: int = 10) -> list[SagaState]:
```

Returns sagas with non-empty `pending_commands` that may not have been fully dispatched (e.g., the process crashed between dispatch calls). Used by `recover_pending_sagas()`.

### `find_suspended_sagas(limit)` — suspension queries

```python
async def find_suspended_sagas(self, limit: int = 10) -> list[SagaState]:
```

Returns sagas in `SUSPENDED` status, for monitoring or manual intervention.

### `find_expired_suspended_sagas(limit)` — timeout handling

```python
async def find_expired_suspended_sagas(self, limit: int = 10) -> list[SagaState]:
```

Returns suspended sagas whose `timeout_at` has passed. Used by `process_timeouts()`.

### `pull_events()` — drain domain events

```python
def pull_events(self) -> list[DomainEvent]:
```

Returns all domain events collected by `save()` since the last call, then clears the internal buffer. This allows the repository to forward saga-emitted domain events to the event bus.

## Why a Protocol?

The `SagaRepository` is a `Protocol` (structural subtyping) rather than an ABC. This means any class with the matching method signatures satisfies the contract — no inheritance required. This design supports:

- **In-memory fakes** for testing (see `FakeSagaRepository`)
- **SQL implementations** (PostgreSQL, SQLite)
- **Document databases** (MongoDB, DynamoDB)
- **Event sourcing** (storing saga state as a stream of saga events)

## Implementing a custom repository

A minimal implementation needs `save`, `get_by_id`, `find_by_correlation_id`, `find_stalled_sagas`, and `find_expired_suspended_sagas`. For testing, use `FakeSagaRepository` from `pydomain.testing.saga` — see [Use a Fake Saga Repository](../../how-to/testing/use-fake-saga-repository.md).

## Next steps

- [Saga Manager](saga-manager.md) — how the manager uses the repository
- [Use a Fake Saga Repository](../../how-to/testing/use-fake-saga-repository.md) — in-memory test double
