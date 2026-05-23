# ADR-003: Async-Only Public API

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

The library provides building blocks for DDD, CQRS, and Event Sourcing. Its consumers are typically web frameworks (FastAPI, Starlette) or message consumers that already run in async contexts. The public API includes repository methods (`save`, `get_by_id`), event store operations (`append_to_stream`, `load_stream`), unit-of-work lifecycle (`commit`, `rollback`), command/query dispatch, projection apply/rebuild, and subscription runners.

A mixed sync/async API forces every consumer to reason about which methods are blocking and which are awaitable. For infrastructure adapters backed by async drivers (`asyncpg`, `motor`, `aioredis`), synchronous signatures are impossible without thread-pool bridges.

## Decision

Every public method across all layers is `async`. Callers always `await`, whether the underlying implementation is I/O-bound or not.

This applies to:

- **Domain layer**: `Repository.save()`, `Repository.get_by_id()`, `Repository.delete()`
- **CQRS layer**: `CommandBus.dispatch()`, `QueryBus.dispatch()`, `UnitOfWork.commit()`, `UnitOfWork.rollback()`, `CommandHandler.__call__()`, `QueryHandler.__call__()`, `EventHandler.__call__()`, `PipelineBehavior.handle()`
- **ES layer**: `EventStore.append_to_stream()`, `EventStore.load_stream()`, `CheckpointStore.load_checkpoint()`, `CheckpointStore.save_checkpoint()`, `SnapshotStore.load_snapshot()`, `SnapshotStore.save_snapshot()`, `EventSourcedProjection.handle()`
- **Infrastructure layer**: `MessageBus.dispatch()`, `MessageBroker.publish()`, `SubscriptionRunner.run()`, `ProcessedCommandStore.get()`, `ProcessedCommandStore.set()`
- **Testing**: Uses `pytest-anyio` + `anyio` for async tests (`asyncio_mode = "auto"`)

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Mixed sync/async API | Forces consumers to track which methods block; complicates composition; thread-pool bridges add latency and complexity |
| Sync-only API | Cannot use async database drivers; blocks the event loop on I/O; poor fit for modern async Python frameworks |
| `sync + async` pair for every method | Doubles the API surface; doubles maintenance; confusing for consumers |

## Consequences

### Positive

- Single, consistent calling convention — every public call is `await`.
- Natural fit for async frameworks (FastAPI, Starlette, anyio).
- No thread-pool bridges or event-loop blocking.
- Simpler mental model: everything is async, no surprises.

### Negative

- Synchronous consumers must wrap calls in `asyncio.run()` or similar.
- Pure in-memory fakes still need `async def` even though they perform no I/O.

### Neutral

- The async-only convention extends to test code via `pytest-anyio`.

## References

- `src/pydomain/ddd/repository.py` — `Repository` Protocol (all methods async)
- `src/pydomain/cqrs/command_bus.py` — `CommandBus.dispatch()`
- `src/pydomain/cqrs/query_bus.py` — `QueryBus.dispatch()`
- `src/pydomain/cqrs/unit_of_work.py` — `UnitOfWork` Protocol
- `src/pydomain/cqrs/handlers.py` — `CommandHandler`, `QueryHandler`, `EventHandler`
- `src/pydomain/es/event_store.py` — `EventStore` Protocol
- `src/pydomain/es/checkpoint_store.py` — `CheckpointStore` Protocol
- `src/pydomain/infrastructure/message_bus.py` — `MessageBus.dispatch()`
