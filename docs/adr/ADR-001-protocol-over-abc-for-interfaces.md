# ADR-001: Protocol over ABC for Interfaces

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

The library defines behavioural contracts: `Repository`, `EventStore`, `SnapshotStore`, `MessageBroker`, `UnitOfWork`, `PipelineBehavior`, `Factory`, `Projection`, and many others. These are consumed by infrastructure adapters written by library users.

`pydomain` users own their infrastructure adapters. A `SqlAlchemyOrderRepository` may already inherit from a SQLAlchemy mixin or a project-specific base. Forcing it to also inherit from a library ABC creates coupling and MRO complications.

## Decision

Use `typing.Protocol` with `@runtime_checkable` for all behavioural interfaces. Reserve `ABC` + `abstractmethod` only for base classes that provide default behaviour.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| ABC for all interfaces | Requires explicit inheritance; conflicts with user base classes; MRO complications |
| Plain functions / duck typing | No `isinstance()` support; no clear contract for users to implement against |

## Consequences

### Positive

- Zero coupling — any class with matching method signatures automatically conforms.
- Multiple interfaces are naturally supported (no diamond problem).
- Users can integrate existing classes without refactoring their inheritance hierarchy.
- `@runtime_checkable` provides `isinstance()` support when needed.

### Negative

- Cannot provide default method implementations in a Protocol.
- Protocol classes are less familiar to some Python developers than ABC.

### Neutral

- ABC is still used where the base class provides substantial default behaviour: `EventSourcedAggregateRoot`, `EventSourcedProjection`, `Specification`.

## References

- §9.1 Protocol over ABC for Interfaces
