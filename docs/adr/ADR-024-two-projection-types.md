# ADR-024: Two Separate Projection Types by Naming Convention

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

The library has two projection abstractions: `Projection[StateT]` in `pydomain.cqrs` and `EventSourcedProjection` in `pydomain.es`. Both transform events into read models. A single type hierarchy would create a layer violation: the CQRS layer (which has no event-sourcing knowledge) would either depend on the ES layer, or the ES layer would provide a CQRS-layer base class. Neither is acceptable under the strict module dependency graph.

## Decision

Two independent types with distinct names and module locations, sharing no inheritance:

- `Projection[StateT]` — a `Protocol` in `pydomain.cqrs` (contract: "what is a projection?")
- `EventSourcedProjection` — an `ABC` in `pydomain.es` (mechanism: "how do I build one from an event stream?")

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Single `Projection` base class | Creates layer violation: `cqrs` must not import from `es`, and `es` must not define CQRS contracts |
| `EventSourcedProjection` extends `Projection` | Same layer violation — ES would depend on CQRS definition |

## Consequences

### Positive

- `cqrs` remains independent of `es` (preserving modularity and the dependency graph).
- A class can satisfy both simultaneously (structural subtyping for `Projection`, inheritance for `EventSourcedProjection`).
- Each module's projection type carries only the concerns relevant to its layer.

### Negative

- Naming overlap may confuse newcomers (see §11.7 — Projection Dual Abstraction Confusion).
- Users must use explicit imports; wildcard imports could shadow one with the other.

## References

- §9.3 Two Separate Projection Types
- §11.7 Projection Dual Abstraction Confusion
