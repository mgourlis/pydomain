# ADR-026: No Generic ReadStore Protocol — User-Defined Read Contracts

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

The library provides `Repository[T, TId]` as a universal Protocol for write-side persistence (`save`, `get_by_id`). On the read side, query handlers need similar decoupling from infrastructure.

`Repository[T, TId]` works because every aggregate needs the same operations: persist changes and load by identity. Read models, however, have fundamentally different query methods shaped by the specific access patterns they serve.

## Decision

The library does **not** provide a generic `ReadStore` Protocol. Users define their own domain-specific read store Protocols in the application layer, one per read model.

The library provides `ProjectionStore` for simple state persistence and `InMemoryProjectionStore` as a test fake.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Generic `ReadStore[T]` Protocol | Too abstract (just `Callable`) or too opinionated (dictates query semantics that vary wildly between read models); adds no value beyond `ProjectionStore` |
| CQRS-specific base classes with filter/page/sort | Read model access patterns are too diverse for a one-size-fits-all interface |

## Consequences

### Positive

- Each read model gets precisely the query methods it needs — no unused generic abstractions.
- Query handlers remain infrastructure-free — they depend on user-defined Protocols.
- The `bootstrap()` composition root wires concrete read store implementations consistently.

### Negative

- Users must define their own Protocols and fakes per read model (more boilerplate, but more precise).

## References

- §9.12 No Generic ReadStore Protocol
