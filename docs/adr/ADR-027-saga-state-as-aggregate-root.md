# ADR-027: Saga State as AggregateRoot

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

`SagaState` tracks a long-running process: step history, processed events, compensation stack, lifecycle transitions. It needs persistence, optimistic concurrency, and event tracking — exactly the same requirements as any aggregate.

## Decision

`SagaState` inherits from `AggregateRoot[UUID]`, gaining all aggregate capabilities (identity, optimistic concurrency via `version`, event collection via `pull_events()`, repository pattern).

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Dedicated `SagaStore` protocol with load/save semantics | Duplicates optimistic concurrency, event collection, and UoW integration that `AggregateRoot` already provides |

## Consequences

### Positive

- Zero additional persistence code — saga state repositories are standard `Repository[SagaState, UUID]` implementations.
- Saga state benefits from the same publish-after-commit semantics as domain aggregates.
- `SagaManager` uses a regular `SagaRepository` (a `Repository[SagaState, UUID]` with a `get_by_saga_type` extension).

### Negative

- Saga state carries aggregate semantics (version, pending events) that may feel heavy for simple state storage.

## References

- §9.6 Saga State as AggregateRoot
