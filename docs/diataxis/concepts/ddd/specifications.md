# Specifications

> **Adoption Level:** 1 — Tactical DDD
> **Module:** `pydomain.ddd.specification`

## What is a Specification?

A **Specification** is a Value Object that encapsulates a business rule as a predicate — it answers the question *"does this candidate satisfy the rule?"*. Specifications are **composable** using logical AND, OR, and NOT operations.

The pattern has three uses:

| Use | Description |
|-----|-------------|
| **Validation** | Check if an object meets business rules |
| **Selection** | Query repositories for objects matching criteria |
| **Generation** | Build objects to satisfy a specification |

## The `Specification` Base Class

```python
class Specification(BaseModel, ABC):
    model_config = ConfigDict(frozen=True)

    @abstractmethod
    def is_satisfied_by(self, obj: Any) -> bool: ...
```

Specifications are:

- **Immutable** (frozen) — once created, their state never changes
- **Abstract** — you implement `is_satisfied_by()` with your domain logic
- **Value Objects** — structural equality, hashable, serializable

## Composing Specifications

Specifications compose with `and_()`, `or_()`, and `not_()`:

```python
class IsHighValue(Specification):
    threshold: int

    def is_satisfied_by(self, order: Any) -> bool:
        return order.total >= self.threshold

class IsPremiumCustomer(Specification):
    def is_satisfied_by(self, order: Any) -> bool:
        return order.customer.is_premium

# Composition
high_value = IsHighValue(threshold=1000)
premium = IsPremiumCustomer()
premium_high_value = high_value.and_(premium)

if premium_high_value.is_satisfied_by(order):
    apply_discount(order)
```

### Composition Methods

| Method | Returns | Satisfied when... |
|--------|---------|-------------------|
| `and_(other)` | `AndSpecification` | Both specs are satisfied |
| `or_(other)` | `OrSpecification` | Either spec is satisfied |
| `not_()` | `NotSpecification` | This spec is NOT satisfied |

## Subsumption

A specification **subsumes** another if every object satisfying the other also satisfies this one (i.e., this spec is a superset):

```python
class IsAdult(Specification):
    def is_satisfied_by(self, person: Any) -> bool:
        return person.age >= 18

class IsSenior(Specification):
    def is_satisfied_by(self, person: Any) -> bool:
        return person.age >= 65

senior = IsSenior()
adult = IsAdult()

# Every senior is an adult, but not every adult is a senior
assert senior.subsumes(adult) is False
assert adult.subsumes(senior) is True
```

Override `subsumes()` in subclasses with domain-specific logic. The default implementation returns `False`.

## Using Specifications with Repositories

Specifications can drive repository queries:

```python
class OrderRepository:
    async def find_satisfying(self, spec: Specification) -> list[Order]:
        # Infrastructure translates the spec into a query
        ...
```

This keeps the selection logic in the domain layer — the repository interface speaks domain language, not SQL.

## When to Use Specifications

Use Specifications when:

- A business rule is **reused** across multiple contexts (validation, selection, generation)
- You need **composable** rules that combine at runtime
- Query criteria should be expressed in **domain language**

Avoid when:

- A simple `if` check is sufficient and won't be reused
- The rule is trivial and unlikely to compose with others

## Next Steps

- **[Implement a Specification →](../../how-to/ddd/implement-specification.md)** — step-by-step guide
- **[Repositories →](repositories.md)** — where specifications drive queries
- **[Value Objects →](value-objects.md)** — the base class for specifications
