# Value Objects

> **Adoption Level:** 1 — Tactical DDD
> **Module:** `pydomain.ddd.value_object`

## What is a Value Object?

A **Value Object** is a domain object defined entirely by its **attributes**, not by identity. Two Value Objects with the same attribute values are considered equal and interchangeable.

Value Objects are **immutable** — once created, their state never changes. Operations that would "modify" a Value Object instead return a **new instance**.

## The `ValueObject` Base Class

```python
from pydantic import ConfigDict
from pydantic import BaseModel


class ValueObject(BaseModel):
    model_config = ConfigDict(frozen=True)
```

That's the entire base class — a frozen Pydantic model. The power comes from what frozen implies:

| Property | Behavior |
|----------|----------|
| **Immutability** | Fields cannot be changed after creation |
| **Structural equality** | Two instances with the same values are equal |
| **Hashable** | Can be used as dictionary keys and in sets |
| **Serializable** | `model_dump()` / `model_validate()` work out of the box |

## No `id` Field

Value Objects have no identity field. This is the defining distinction from [Entities](entities.md):

```python
# Value Object — equality by attributes
money_a = Money(amount=1000, currency="EUR")
money_b = Money(amount=1000, currency="EUR")
assert money_a == money_b  # True — same attributes

# Entity — equality by identity
order_a = Order(id=same_uuid, total=1000)
order_b = Order(id=same_uuid, total=2000)
assert order_a == order_b  # True — same identity despite different total
```

## Closure of Operations

Value Objects follow the **closure-of-operations** pattern — operations on a Value Object return a new Value Object of the same type:

```python
class Money(ValueObject):
    amount: int
    currency: str

    def add(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise ValueError("Cannot add different currencies")
        return self.model_copy(update={"amount": self.amount + other.amount})

    def multiply(self, factor: int) -> Money:
        return self.model_copy(update={"amount": self.amount * factor})
```

Use `model_copy(update=...)` — Pydantic v2's method for creating a shallow copy with specified field changes. Never mutate in place.

## Validation on Construction

Since Value Objects are Pydantic models, you get validation for free:

```python
from pydantic import field_validator


class EmailAddress(ValueObject):
    address: str

    @field_validator("address")
    @classmethod
    def must_contain_at(cls, v: str) -> str:
        if "@" not in v:
            raise ValueError("Invalid email address")
        return v
```

## When to Use a Value Object

Use a Value Object when:

- The object has **no identity** — it's defined entirely by its attributes
- **Immutability** is appropriate — the concept doesn't change over time
- **Structural equality** makes sense — two objects with the same values are interchangeable
- You want to **encapsulate validation rules** (email format, currency codes, measurement ranges)

Common examples: `Money`, `EmailAddress`, `Address`, `DateRange`, `Measurement`, `Percentage`.

## Immutability and Frozen Models

Value Objects use `frozen=True` in Pydantic's `ConfigDict`. Attempting to modify a field raises `ValidationError`:

```python
money = Money(amount=1000, currency="EUR")
money.amount = 2000  # Raises ValidationError
```

This is intentional. If you need mutable state, use an [Entity](entities.md) instead.

## Relationship to Domain Events

[Domain Events](domain-events.md) are themselves Value Objects — they are frozen, immutable records of facts. They inherit from `DomainEvent` (which is a frozen Pydantic model) rather than directly from `ValueObject`, but they share the same immutability and structural equality guarantees.

## Next Steps

- **[Create a Value Object →](../../how-to/ddd/create-value-object.md)** — step-by-step guide
- **[Entities →](entities.md)** — when you need identity
- **[Specifications →](specifications.md)** — composable business rules as Value Objects
