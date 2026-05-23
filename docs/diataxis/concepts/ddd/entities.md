# Entities

> **Adoption Level:** 1 — Tactical DDD
> **Module:** `pydomain.ddd.entity`

## What is an Entity?

An **Entity** is a domain object defined by its **identity**, not its attributes. Two entities are considered equal if they share the same `id`, even if all other fields differ.

This is the fundamental distinction from [Value Objects](value-objects.md): an Entity has a stable identity that persists across state changes, while a Value Object is defined entirely by its current attribute values.

## Why Identity Matters

Consider a person changing their name. The person is still the *same person* — their identity hasn't changed, only their attributes. In pydomain, this is an `Entity`:

```python
from uuid import UUID
from pydomain.ddd.entity import Entity


class Person(Entity[UUID]):
    name: str
    email: str
```

Two `Person` instances with different names but the same `id` are equal:

```python
p1 = Person(id=some_uuid, name="Alice", email="alice@example.com")
p2 = Person(id=some_uuid, name="Alice Smith", email="alice.s@example.com")

assert p1 == p2  # True — same identity
```

## The `Entity[TId]` Base Class

```python
class Entity[TId](BaseModel):
    id: TId
    version: int = 0
```

| Field | Type | Purpose |
|-------|------|---------|
| `id` | `TId` | Unique identity — `UUID`, `int`, `str`, or any hashable type |
| `version` | `int` | Optimistic concurrency counter, starts at 0 |

### Key Design Decisions

**Mutable (`frozen=False`).** Unlike Value Objects, entities are mutable — their state changes over time. The `id` field stays constant, but other fields can be updated through methods.

**Identity equality.** `__eq__` compares `type(self) is type(other) and self.id == other.id`. This means two different entity types with the same `id` value are *not* equal.

**Auto-generated ID.** When `id` is omitted at construction, the configured `IdGenerator` produces one automatically. A runtime type guard verifies the generated value matches the declared `TId` annotation.

## Auto-ID Generation

When you create an entity without specifying `id`, pydomain auto-generates one:

```python
order = Order(customer_id=customer_id, total=1000)
# order.id is now a UUIDv7 — generated automatically
```

The default generator is `Uuid7Generator`, which produces time-ordered UUIDv7 identifiers. You can configure a different generator at startup:

```python
from pydomain.ddd.entity import Entity
from pydomain.ddd.id_generator import Uuid7Generator

Entity.configure(id_generator=Uuid7Generator())
```

If the generator produces a type that doesn't match the entity's `TId` annotation (e.g., a `SnowflakeIdGenerator` that returns `int` but the entity expects `UUID`), a `DomainError` is raised at construction time.

See [Entity Identity](entity-identity.md) for a deep dive into ID generation.

## When to Use an Entity

Use an Entity when:

- The object has a **stable identity** that persists across state changes
- You need to **track it over time** (e.g., a customer, order, account)
- **Equality is based on identity**, not attribute values

Use a [Value Object](value-objects.md) when:

- The object has **no identity** — it's defined entirely by its attributes
- **Equality is structural** — two objects with the same values are interchangeable
- The object is **immutable** — operations return new instances

## Mutation Methods

Entities are mutable — state changes happen through **methods**, not by setting attributes directly. This lets the entity enforce invariants:

```python
from pydomain.ddd.exceptions import DomainError


class InvalidProductName(DomainError):
    """Business rule: product name must not be empty."""


class Product(Entity[UUID]):
    name: str
    price_cents: int
    is_available: bool = True

    def rename(self, new_name: str) -> None:
        if not new_name.strip():
            raise InvalidProductName("Name cannot be empty")
        self.name = new_name

    def discontinue(self) -> None:
        self.is_available = False
```

### Validation Tiers

Entities enforce rules through two complementary mechanisms:

| Mechanism | For | Raises | When |
|-----------|-----|--------|------|
| `@field_validator` | Structural constraints (always true) | `ValueError` | Construction time |
| `DomainError` subclass | Business rules (state-dependent) | `DomainError` | Mutation method |

**Structural constraints** — rules that are always true regardless of context, like "email must contain `@`". These use Pydantic `@field_validator` and raise `ValueError` at construction time.

**Business rules** — rules about *when* something can happen, like "cannot rename a discontinued product". These use `DomainError` subclasses named in the Ubiquitous Language and are raised in mutation methods.

> **Rule of thumb:** If the rule would make sense in a Pydantic model without business context, use a validator. If the rule is about state transitions or business policies, use a `DomainError`.

## Relationship to Aggregate Roots

`AggregateRoot[TId]` inherits from `Entity[TId]` and adds domain event management. Every Aggregate Root is an Entity, but not every Entity is an Aggregate Root. See [Aggregates](aggregates.md) for the full picture.

## Next Steps

- **[Define an Entity →](../../how-to/ddd/define-entity.md)** — step-by-step guide with validation
- **[Value Objects →](value-objects.md)** — the identity-less counterpart
- **[Entity Identity →](entity-identity.md)** — deep dive into ID generation
