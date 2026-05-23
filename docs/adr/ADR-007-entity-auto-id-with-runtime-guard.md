# ADR-007: Entity Auto-ID with Runtime Type Guard

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Every `Entity[TId]` has an `id` field. Previously, only `Entity[UUID]` subclasses got auto-generated IDs. This forced developers writing `Entity[int]` or `Entity[str]` to always provide `id` explicitly, even when a suitable generator existed.

Python's generic type parameters are erased at runtime, so the library cannot statically verify that an `IdGenerator[TId]` produces the correct type.

## Decision

Auto-generate `id` via the configured `IdGenerator[TId]` for **all** `TId` types when `id` is omitted. A runtime type guard raises `DomainError` if the generator produces a value that does not match the declared `TId` annotation.

Auto-generation runs in a `model_validator(mode="before")` so the `id` field is always populated by the time field validators run.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Auto-generate only for `Entity[UUID]` | Forces explicit ID provision for all other types; special-cases UUID |
| `Optional[UUID]` / `Field(default_factory=...)` on every subclass | Pushes boilerplate to every entity definition |
| No runtime guard | Silent type mismatch when generator type doesn't match entity ID type |

## Consequences

### Positive

- `Entity[int]`, `Entity[str]`, `Entity[UUID]` all work with auto-generation if a matching generator is configured.
- Mismatch between generator output and entity ID type is caught immediately with a clear error.
- No boilerplate on subclasses — `id` is populated automatically.

### Negative

- Runtime type checking for what is conceptually a static type constraint (a trade-off of Python's type erasure).

## References

- §9.9 Entity Auto-ID vs Explicit ID
