# ADR-014: `frozen=True` and `extra="forbid"` on Commands and Queries

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Commands and queries are user input DTOs. They carry all data the handler needs. A command represents an immutable intent — once created, it must not be modified. Fields should be explicit contracts; typos or wrong fields should fail immediately.

## Decision

Both `Command[TResult]` and `Query[TResult]` use `frozen=True` and `extra="forbid"`.

`hydrate_command()` in the saga module strips unknown keys before `model_validate()`, making deserialization resilient to schema evolution.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Mutable commands | Handler could accidentally modify input; breaks intent-as-value semantics |
| `extra="ignore"` | Typos and wrong fields are silently swallowed — bugs at the application/handler boundary go undetected |

## Consequences

### Positive

- Commands are immutable — safe to cache, pass around, and reason about.
- Typos in field names fail immediately at construction (e.g., `custoemr_id`).
- `hydrate_command()` handles schema evolution by stripping extra keys on deserialization.

### Negative

- All fields must be present when reconstructing from stored data (the `hydrate_command()` helper addresses this for sagas).

## References

- §9.10 Frozen Commands and Queries
