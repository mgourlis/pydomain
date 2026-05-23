# ADR-006: Entity Identity Semantics — Equality by `id`, Mutable State

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

In DDD, an Entity is defined by its identity, not by its attributes. Two `Customer` objects with different names but the same `id` are the same customer. This is the fundamental distinction from Value Objects, which are defined by their attributes and are immutable.

Python's default `BaseModel` equality is structural (all fields compared). For entities, this is wrong — changing a customer's name should not change its identity. Pydantic's default `__hash__` is also structural when `frozen=True`, but raises `TypeError` when `frozen=False` (because mutable objects should not be hashable by default).

Entities must be hashable (for use in sets and as dict keys) and mutable (state changes are expected in DDD).

## Decision

`Entity[TId]` overrides both `__eq__` and `__hash__`:

- **Equality**: `type(self) is type(other) and self.id == other.id`. Two entities are equal if and only if they share the same concrete type and the same `id`.
- **Hash**: `hash(self.id)`. Hashing is by identity, not by attributes.
- **Mutability**: `model_config = ConfigDict(frozen=False)`. State changes are expected.

This gives entities set/dict membership semantics based on identity, while allowing attribute mutation.

```python
class Entity[TId](BaseModel):
    id: TId
    version: int = 0
    model_config = ConfigDict(frozen=False)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Entity):
            return False
        return type(self) is type(other) and self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)
```

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Default Pydantic equality (structural) | Two entities with same `id` but different attributes would be "different" — violates DDD identity semantics |
| `frozen=True` with identity equality | Entities must be mutable — DDD expects state changes through methods |
| No `__hash__` override (unhashable) | Cannot use entities in sets or as dict keys; limits usage patterns |
| Hash by `(type, id)` tuple | Slightly more correct but `hash(self.id)` is sufficient since `type` is checked in `__eq__` |

## Consequences

### Positive

- Correct DDD identity semantics: equality and hashing by `id` only.
- Entities can be used in sets and as dict keys (membership by identity).
- State mutation is allowed — the aggregate can change attributes through methods.
- `version` field supports optimistic concurrency without affecting identity.

### Negative

- Hashing by `id` alone means two different entity types with the same `id` value will collide in hash-based structures (mitigated by `__eq__` checking `type(self) is type(other)`).
- Mutable entities require care in concurrent contexts — the `version` field addresses this for persistence.

### Neutral

- `ValueObject` uses `frozen=True` with default structural equality — the exact opposite of this decision. This asymmetry is intentional and mirrors DDD's entity-vs-value-object distinction.

## References

- `src/pydomain/ddd/entity.py` — `Entity[TId]` class with `__eq__` and `__hash__`
- `src/pydomain/ddd/value_object.py` — `ValueObject` with `frozen=True` (contrast)
