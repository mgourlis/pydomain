# How to Create a Value Object

<!-- docs-test: runnable -->

> **Prerequisite:** [Value Objects concept](../../concepts/ddd/value-objects.md)

## Problem

You need an immutable domain object defined by its attributes, with no identity.

## Solution

Subclass `ValueObject` and define your fields:

```python
from pydomain.ddd.value_object import ValueObject


class Money(ValueObject):
    amount: int
    currency: str
```

## Steps

### 1. Define the Value Object

```python
from pydomain.ddd.value_object import ValueObject


class EmailAddress(ValueObject):
    address: str
```

### 2. Add validation

Use Pydantic validators for field-level rules:

```python
from pydantic import field_validator


class EmailAddress(ValueObject):
    address: str

    @field_validator("address")
    @classmethod
    def must_be_valid_email(cls, v: str) -> str:
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError(f"Invalid email address: {v}")
        return v
```

### 3. Add operations that return new instances

Build the result by calling the class, so every validator runs on the new instance. Do not use `model_copy(update=...)`: it skips validation, so `multiply(-1)` would return a negative `Money` past the validator:

```python
class Money(ValueObject):
    amount: int
    currency: str

    @field_validator("amount")
    @classmethod
    def amount_must_be_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Amount cannot be negative")
        return v

    def add(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise ValueError(f"Cannot add {other.currency} to {self.currency}")
        return type(self)(amount=self.amount + other.amount, currency=self.currency)

    def subtract(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise ValueError(f"Cannot subtract {other.currency} from {self.currency}")
        result = self.amount - other.amount
        if result < 0:
            raise ValueError("Result cannot be negative")
        return type(self)(amount=result, currency=self.currency)

    def multiply(self, factor: int) -> Money:
        return type(self)(amount=self.amount * factor, currency=self.currency)

    def format(self) -> str:
        return f"{self.amount / 100:.2f} {self.currency}"
```

### 4. Use the Value Object

```python
price = Money(amount=1000, currency="EUR")
tax = Money(amount=210, currency="EUR")
total = price.add(tax)

print(total.format())  # "12.10 EUR"
print(price == Money(amount=1000, currency="EUR"))  # True — structural equality
```

## Complete Example — Address

```python
from pydomain.ddd.value_object import ValueObject
from pydantic import field_validator


class Address(ValueObject):
    street: str
    city: str
    postal_code: str
    country: str

    @field_validator("postal_code")
    @classmethod
    def postal_code_must_be_formatted(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Postal code cannot be empty")
        return v

    @field_validator("country")
    @classmethod
    def country_must_be_iso_code(cls, v: str) -> str:
        if len(v) != 2:
            raise ValueError("Country must be a 2-letter ISO code")
        return v.upper()

    def format(self) -> str:
        return f"{self.street}, {self.postal_code} {self.city}, {self.country}"


# Usage
hq = Address(street="123 Main St", city="Athens", postal_code="10434", country="GR")
print(hq.format())  # "123 Main St, 10434 Athens, GR"

# Structural equality
same = Address(street="123 Main St", city="Athens", postal_code="10434", country="GR")
assert hq == same
```

## Testing

```python
def test_money_add():
    a = Money(amount=100, currency="EUR")
    b = Money(amount=50, currency="EUR")
    result = a.add(b)
    assert result == Money(amount=150, currency="EUR")
    assert a == Money(amount=100, currency="EUR")  # Original unchanged

def test_money_add_different_currency_raises():
    a = Money(amount=100, currency="EUR")
    b = Money(amount=50, currency="USD")
    import pytest
    with pytest.raises(ValueError, match="Cannot add"):
        a.add(b)

def test_money_multiply_runs_the_validator():
    import pytest
    with pytest.raises(ValueError, match="cannot be negative"):
        Money(amount=100, currency="EUR").multiply(-1)

def test_money_immutability():
    money = Money(amount=100, currency="EUR")
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        money.amount = 200  # type: ignore
```

<!-- docs-test: check
test_money_add()
test_money_add_different_currency_raises()
test_money_multiply_runs_the_validator()
test_money_immutability()
-->

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Adding an `id` field | Value Objects have no identity — remove it |
| Mutating in place, or copying with `model_copy(update=...)` | Build a new instance with `type(self)(...)`; `model_copy` skips the validators |
| Using `frozen=False` | `ValueObject` is always frozen — don't override this |

## See Also

- [Value Objects concept](../../concepts/ddd/value-objects.md)
- [Define an Entity how-to](define-entity.md) — when you need identity
- [Specifications concept](../../concepts/ddd/specifications.md) — Value Objects as business rules
