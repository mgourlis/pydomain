# How to Define an Entity

> **Prerequisite:** [Entities concept](../../concepts/ddd/entities.md)

## Problem

You need a domain object with a stable identity that persists across state changes.

## Solution

Subclass `Entity[TId]`, specify the identity type, and add your domain fields:

```python
from uuid import UUID
from pydomain.ddd.entity import Entity
from pydomain.ddd.exceptions import DomainError


class InvalidProductName(DomainError):
    """Raised when a product name is invalid."""


class Customer(Entity[UUID]):
    name: str
    email: str
    is_active: bool = True
```

## Steps

### 1. Choose the identity type

Common choices:

| Type | Use When |
|------|----------|
| `UUID` | Default — time-ordered UUIDv7, globally unique |
| `int` | Snowflake-style IDs, database sequences |
| `str` | ULID, slug-based identifiers |

### 2. Define the entity class

```python
from uuid import UUID
from pydomain.ddd.entity import Entity


class Product(Entity[UUID]):
    name: str
    price_cents: int
    is_available: bool = True
```

### 3. Add mutation methods

Entities are mutable — state changes happen through methods:

```python
from pydomain.ddd.exceptions import DomainError


class InvalidProductName(DomainError):
    """Business rule: product name must not be empty."""


class InvalidPrice(DomainError):
    """Business rule: price must be non-negative."""


class Product(Entity[UUID]):
    name: str
    price_cents: int
    is_available: bool = True

    def rename(self, new_name: str) -> None:
        if not new_name.strip():
            raise InvalidProductName("Name cannot be empty")
        self.name = new_name

    def update_price(self, new_price_cents: int) -> None:
        if new_price_cents < 0:
            raise InvalidPrice("Price cannot be negative")
        self.price_cents = new_price_cents

    def discontinue(self) -> None:
        self.is_available = False
```

### 4. Add validation with Pydantic

```python
from pydantic import field_validator


class Product(Entity[UUID]):
    name: str
    price_cents: int
    is_available: bool = True

    @field_validator("price_cents")
    @classmethod
    def price_must_be_positive(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Price cannot be negative")
        return v

    @field_validator("name")
    @classmethod
    def name_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name cannot be empty")
        return v
```

> **Why `ValueError` here and `DomainError` in mutation methods?** Pydantic validators enforce *structural* constraints that are always true ("a price is never negative"). Mutation methods enforce *business* rules that depend on context ("you can only rename an active product"). See [Validation Strategy](#validation-strategy) below.

### 5. Create instances

```python
# Auto-generated id
product = Product(name="Widget", price_cents=999)
print(product.id)       # UUIDv7 — auto-generated
print(product.version)  # 0

# Explicit id (for reconstitution)
from uuid import uuid4
existing_id = uuid4()
product = Product(id=existing_id, name="Widget", price_cents=999)
```

## Complete Example

```python
from uuid import UUID
from pydantic import field_validator
from pydomain.ddd.entity import Entity
from pydomain.ddd.exceptions import DomainError


class InvalidEmail(DomainError):
    """Business rule: email must be valid."""


class Customer(Entity[UUID]):
    name: str
    email: str
    is_active: bool = True

    @field_validator("email")
    @classmethod
    def email_must_contain_at(cls, v: str) -> str:
        if "@" not in v:
            raise ValueError("Invalid email address")
        return v

    def update_email(self, new_email: str) -> None:
        if "@" not in new_email:
            raise InvalidEmail("Invalid email address")
        self.email = new_email

    def deactivate(self) -> None:
        self.is_active = False

    def activate(self) -> None:
        self.is_active = True


# Usage
customer = Customer(name="Alice", email="alice@example.com")
customer.update_email("alice.smith@example.com")
customer.deactivate()

assert customer.is_active is False
```

## Validation Strategy

Entities use a **two-tier validation approach**:

### Tier 1: Pydantic validators — structural constraints

Rules that are **always true** regardless of business state. They run at construction time:

```python
from pydantic import field_validator


class Customer(Entity[UUID]):
    email: str

    @field_validator("email")
    @classmethod
    def email_must_contain_at(cls, v: str) -> str:
        if "@" not in v:
            raise ValueError("Invalid email address")
        return v
```

Use `ValueError` here — Pydantic expects it. These rules guarantee the object is **structurally sound** before it enters the domain.

### Tier 2: Domain exceptions — business rules in mutation methods

Rules that **depend on state** or carry **business meaning**. Raised inside mutation methods:

```python
from pydomain.ddd.exceptions import DomainError


class InvalidEmail(DomainError):
    """Business rule: email must be valid."""


class Customer(Entity[UUID]):
    email: str
    is_active: bool = True

    def update_email(self, new_email: str) -> None:
        if "@" not in new_email:
            raise InvalidEmail("Invalid email address")
        self.email = new_email
```

Domain exceptions are **named in the Ubiquitous Language** — they carry business meaning and can be caught at the application layer (e.g., mapped to HTTP 400 Bad Request).

### When to use which

| Rule Kind | Mechanism | Example | When it Runs |
|-----------|-----------|---------|-------------|
| Field format is always invalid | `@field_validator` + `ValueError` | Email without `@` | Construction time |
| State-dependent business rule | Method + `DomainError` subclass | "Cannot rename discontinued product" | Mutation method |
| Reusable / composable rule | `Specification` + `DomainError` | Active customer check in 3 places | Any context |

> **Rule of thumb:** If the rule would make sense in a Pydantic model without business context ("this string must contain @"), use a validator. If the rule is about *when* something can happen (state transitions, business policies), use a `DomainError`.

## Testing

```python
def test_customer_equality_by_identity():
    same_id = uuid4()
    a = Customer(id=same_id, name="Alice", email="a@example.com")
    b = Customer(id=same_id, name="Bob", email="b@example.com")
    assert a == b  # Same identity

def test_customer_inequality():
    a = Customer(name="Alice", email="a@example.com")
    b = Customer(name="Alice", email="a@example.com")
    assert a != b  # Different auto-generated IDs

def test_deactivate():
    customer = Customer(name="Alice", email="a@example.com")
    customer.deactivate()
    assert customer.is_active is False
```

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Adding `id` to constructor when it should be auto-generated | Omit `id` — let the `IdGenerator` produce it |
| Using `frozen=True` on an entity | Entities are mutable — use `frozen=False` (the default for `Entity`) |
| Comparing entities by attribute values | `__eq__` compares identity — use `a.id == b.id` or `a == b` |
| Using `ValueError` for business rule violations | Use `DomainError` subclasses named in the Ubiquitous Language |

## See Also

- [Entities concept](../../concepts/ddd/entities.md)
- [Value Objects how-to](create-value-object.md) — when you don't need identity
- [Entity Identity](../../concepts/ddd/entity-identity.md) — ID generation deep dive
