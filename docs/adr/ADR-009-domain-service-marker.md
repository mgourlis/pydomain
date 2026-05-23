# ADR-009: DomainService as a Marker Class, Not a Base Class

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Domain services hold business logic that doesn't belong to any single entity or value object. Python naturally favours standalone functions over classes for stateless operations. Forcing everything into a class hierarchy adds ceremony without value. However, some operations genuinely benefit from being methods on a service class (e.g., when the service accepts injected dependencies).

## Decision

`DomainService` is a lightweight architectural marker with `__slots__ = ()` and no methods. It signals that a class belongs to the domain layer.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| ABC with abstract methods | Over-engineered for what is often a single function; Python favours functions for stateless operations |
| No marker at all | No architectural signal that a class belongs to the domain layer |

## Consequences

### Positive

- Standalone functions remain first-class citizens — no marker needed for them.
- Classes that *do* exist get a clear architectural signal via `DomainService`.
- `__slots__ = ()` prevents accidental instance state, reinforcing the "stateless" contract.

### Negative

- No runtime or type-checking enforcement that a class inheriting from `DomainService` is actually stateless.

## References

- §9.11 DomainService as a Marker Class
