# ADR-013: Factory vs ReconstitutionFactory — Separate Protocols for Creation and Rebuilding

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Domain objects have two distinct lifecycle phases:

1. **Creation**: A new aggregate is created from command data. The factory may generate a new tracking identity, run validation, and produce a fully initialised object.
2. **Reconstitution**: An existing aggregate is rebuilt from persisted state (event stream replay, database row). The identity must be preserved — no new ID is generated.

Mixing these two operations in a single `create()` method is dangerous: a reconstitution call could accidentally assign a new identity, silently corrupting the aggregate's audit trail. The method signatures are also fundamentally different — creation takes business parameters, reconstitution takes persisted data.

## Decision

Two distinct `Protocol` types for two lifecycle phases:

```python
@runtime_checkable
class Factory[T](Protocol):
    def create(self, *args: Any, **kwargs: Any) -> T: ...

@runtime_checkable
class ReconstitutionFactory[T](Protocol):
    def reconstitute(self, *args: Any, **kwargs: Any) -> T: ...
```

- **`Factory[T]`**: Encapsulates complex creation. Returns a fully constructed domain object. Any class with a `create` method returning `T` structurally conforms. May assign a new tracking identity.

- **`ReconstitutionFactory[T]`**: Rebuilds domain objects from persisted state. Must **never** generate a new tracking ID — identity comes from the persisted data. Any class with a `reconstitute` method returning `T` structurally conforms.

Both are `@runtime_checkable` Protocols (following ADR-001) — no base class inheritance required.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Single `Factory` with `create()` for both | Risk of accidental identity reassignment during reconstitution; ambiguous method signature |
| No factory abstraction | Complex creation logic leaks into command handlers; no encapsulation of construction invariant |
| ABC instead of Protocol | Forces explicit inheritance; conflicts with user base classes (same rationale as ADR-001) |
| `create(from_persistence=True)` flag | Temporal coupling — boolean flag selects behaviour; easy to forget; poor API design |

## Consequences

### Positive

- Clear separation of creation vs reconstitution — impossible to accidentally mix them.
- Each protocol carries a single responsibility — `Factory` for creation, `ReconstitutionFactory` for rebuilding.
- Structural subtyping — any class with the right method conforms, no inheritance needed.
- Method name (`create` vs `reconstitute`) makes intent explicit at the call site.

### Negative

- Two protocols instead of one — slightly more concepts to learn.

### Neutral

- Concrete factory classes can implement both protocols simultaneously if desired (structural subtyping allows this).

## References

- `src/pydomain/ddd/factory.py` — `Factory[T]`, `ReconstitutionFactory[T]` Protocols
- ADR-001: Protocol over ABC for Interfaces
