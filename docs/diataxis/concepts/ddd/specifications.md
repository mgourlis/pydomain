# Specifications

<!-- docs-test: runnable -->

<!-- docs-test: setup
from types import SimpleNamespace
from typing import Any

from pydomain.ddd.specification import Specification


class Order:
    pass


order = SimpleNamespace(total=1500, customer=SimpleNamespace(is_premium=True))


def apply_discount(order: Any) -> None:
    pass
-->

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

<!-- docs-test: skip -->
```python
class Specification(BaseModel, ABC):
    model_config = ConfigDict(frozen=True)

    @abstractmethod
    def is_satisfied_by(self, obj: Any) -> bool: ...
```

Specifications are:

- **Immutable** (frozen) — once created, their state never changes
- **Abstract** — you implement `is_satisfied_by()` with your domain logic
- **Value Objects** — structural equality and hashable
- **Dumpable, not loadable** — `model_dump()` and `model_dump_json()` show a specification's fields, including
  every child of a composite (see [Serialization](#serialization))

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

`and_` and `or_` short-circuit from left to right, like Python's `and` and `or`. Composites are evaluated with an
explicit stack rather than by recursion, so a chain of thousands of `and_()` calls works
([ADR-070](../../../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md)). A subclass of
`AndSpecification`, `OrSpecification` or `NotSpecification` that overrides `is_satisfied_by()` or `subsumes()`
is called as written.

## Subsumption

A specification **subsumes** another if every object satisfying the other also satisfies this one (i.e., this spec is a superset).

The library cannot work this out from `is_satisfied_by()`, so the default `subsumes()` returns `False`: two
specifications that only implement `is_satisfied_by()` never subsume each other, not even themselves. Override
`subsumes()` with the domain rule:

```python
class MinimumAge(Specification):
    minimum: int

    def is_satisfied_by(self, person: Any) -> bool:
        return person.age >= self.minimum

    def subsumes(self, other: Specification) -> bool:
        return isinstance(other, MinimumAge) and self.minimum <= other.minimum


adult = MinimumAge(minimum=18)
senior = MinimumAge(minimum=65)

# Every senior is an adult, but not every adult is a senior
assert adult.subsumes(senior) is True
assert senior.subsumes(adult) is False
```

The composites combine their children's answers: an `AndSpecification` subsumes `other` when every child does,
an `OrSpecification` when any child does, and a `NotSpecification` never does.

## Serialization

A composite dumps each child's own fields, nested, so a rule can be logged or shown:

```python
class MinimumTotal(Specification):
    threshold: int

    def is_satisfied_by(self, order: Any) -> bool:
        return order.total >= self.threshold


rule = MinimumTotal(threshold=100).and_(MinimumTotal(threshold=500).not_())

assert rule.model_dump(mode="json") == {
    "specifications": [
        {"threshold": 100},
        {"specification": {"threshold": 500}},
    ]
}
```

The dump has no round trip: it carries no type name, so `AndSpecification.model_validate(dump)` cannot know
which leaf class each child was. Rebuild stored rules in code. A type-tagged round trip is deferred to a later
feature ([ADR-070](../../../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md)). Dumps of composites nested more than a few hundred levels deep fail in Pydantic's
serializer, although such composites evaluate correctly.

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
