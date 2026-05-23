# ADR-002: Pydantic v2 Only — No v1 Compatibility Shims

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Pydantic v1 and v2 have incompatible APIs. Many existing Python projects still use v1. The library could support both via compatibility shims.

`AggregateRoot._pending_events` relies on Pydantic v2's `PrivateAttr` semantics (excluded from `model_dump()`, excluded from equality). The v1 equivalent (`Field(exclude=True)`) behaves differently.

## Decision

Target `pydantic >= 2.7` exclusively. No v1 compatibility layer.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| v1/v2 shim layer | Large, fragile, prevents v2-only features (`PrivateAttr`, `computed_field`), doubles maintenance |
| Support v1 only | Misses performance gains of v2's Rust core; ecosystem is moving to v2 |

## Consequences

### Positive

- Can freely use v2-only features without conditional logic.
- Significantly better performance due to Pydantic v2's Rust core.
- Reduced maintenance burden — single code path.
- The Python ecosystem is moving to v2; aligning with the trend.

### Negative

- Users on Pydantic v1 must migrate to v2 to use `pydomain`.
- Future Pydantic v3 may require another migration (see §11.4).

## References

- §9.2 Pydantic v2 Only
- §11.4 Dependency Version Risks
