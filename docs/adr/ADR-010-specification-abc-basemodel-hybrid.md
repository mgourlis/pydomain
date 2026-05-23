# ADR-010: Specification ABC+BaseModel Hybrid — Exception to Protocol Rule

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

ADR-001 establishes that all behavioural interfaces use `typing.Protocol` with `@runtime_checkable` instead of ABC. This works well for interfaces with many possible implementations where structural subtyping is desirable.

`Specification` is a specialised Value Object that encapsulates a business rule as a predicate (`is_satisfied_by`). Unlike repository or event store interfaces, specifications have a **required method** — every specification must implement `is_satisfied_by`. Using Protocol here would allow any class with an `is_satisfied_by` method to satisfy the contract accidentally, which is too loose for a domain rule abstraction.

Additionally, specifications are composable: `and_()`, `or_()`, `not_()` must return frozen composite specification instances. These compositional methods need a concrete base class to return correctly typed instances.

## Decision

`Specification` is a **hybrid** of `BaseModel` and `ABC` — the one deliberate exception to ADR-001:

```python
class Specification(BaseModel, ABC):
    model_config = ConfigDict(frozen=True)

    @abstractmethod
    def is_satisfied_by(self, obj: Any) -> bool: ...

    def and_(self, other: Specification) -> AndSpecification: ...
    def or_(self, other: Specification) -> OrSpecification: ...
    def not_(self) -> NotSpecification: ...
    def subsumes(self, other: Specification) -> bool: ...
```

The ABC is justified because:
1. `is_satisfied_by` is **required** — `@abstractmethod` enforces this at instantiation time.
2. Composable combinators (`and_`, `or_`, `not_`) need to return concrete `AndSpecification`, `OrSpecification`, `NotSpecification` types.
3. `subsumes()` provides a default implementation that subclasses can override.

Three composite types are provided as concrete frozen models:
- `AndSpecification` — satisfied when **all** contained specs are satisfied.
- `OrSpecification` — satisfied when **any** contained spec is satisfied.
- `NotSpecification` — satisfied when the contained spec is **not** satisfied.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Pure Protocol for Specification | `is_satisfied_by` is too generic — any class with a matching method would accidentally conform; no `@abstractmethod` enforcement |
| Plain functions for predicates | Cannot compose (AND/OR/NOT); no serialization; no subsumption; loses type safety |
| `BaseModel` without ABC | Cannot enforce `is_satisfied_by` implementation — silent `NotImplementedError` at runtime instead of clear `TypeError` at instantiation |

## Consequences

### Positive

- `@abstractmethod` ensures every specification implements `is_satisfied_by` — fail-fast at instantiation.
- Composable combinators return correctly typed frozen composite specifications.
- `frozen=True` ensures specifications are immutable Value Objects.
- `subsumes()` enables optimisation (e.g., skip redundant database queries).

### Negative

- Requires explicit inheritance (`class MySpec(Specification)`) — structural subtyping is not possible.
- Inconsistent with ADR-001 (Protocol everywhere else) — this is a documented exception.

### Neutral

- The ABC+BaseModel hybrid is unique to `Specification`. No other library type uses this pattern.

## References

- `src/pydomain/ddd/specification.py` — `Specification`, `AndSpecification`, `OrSpecification`, `NotSpecification`
- ADR-001: Protocol over ABC for Interfaces
