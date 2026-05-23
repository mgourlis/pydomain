# How to Configure Entity Identity

> **Prerequisite:** [Entity Identity concept](../../concepts/ddd/entity-identity.md)

## Problem

You want to control how entity IDs are generated — either use a custom strategy or configure the default.

## Solution

Use `Entity.configure()` to set a global generator, or override `_id_generator` on a specific entity subclass.

## Steps

### 1. Use the default (UUIDv7)

No configuration needed — `Uuid7Generator` is the default:

```python
from uuid import UUID
from pydomain.ddd.entity import Entity


class Order(Entity[UUID]):
    customer_id: UUID
    total: int


# id is auto-generated as UUIDv7
order = Order(customer_id=uuid4(), total=1000)
print(type(order.id))  # <class 'uuid.UUID'>
```

### 2. Configure a global generator

Call once at application startup:

```python
from pydomain.ddd.entity import Entity
from pydomain.ddd.id_generator import Uuid7Generator

# Affects all Entity subclasses that don't override _id_generator
Entity.configure(id_generator=Uuid7Generator())
```

### 3. Override per-entity

Set `_id_generator` as a class variable on a specific entity:

```python
from typing import ClassVar
from pydomain.ddd.entity import Entity
from pydomain.ddd.id_generator import IdGenerator


class SnowflakeGenerator(IdGenerator[int]):
    def __init__(self, worker_id: int) -> None:
        self._worker_id = worker_id

    def generate(self) -> int:
        # ... Snowflake algorithm ...
        return 1234567890


class Product(Entity[int]):
    _id_generator: ClassVar[IdGenerator[int]] = SnowflakeGenerator(worker_id=1)
    name: str
    price: int


# id is auto-generated as int
product = Product(name="Widget", price=999)
print(type(product.id))  # <class 'int'>
```

### 4. Create a custom IdGenerator

Implement the `IdGenerator[TId]` protocol — explicit inheritance recommended:

```python
from uuid import UUID
from pydomain.ddd.id_generator import IdGenerator


class UlidGenerator(IdGenerator[str]):
    """Generates ULID strings."""
    def generate(self) -> str:
        import ulid
        return str(ulid.new())


# Configure globally
Entity.configure(id_generator=UlidGenerator())
```

### 5. Reconstitute with an existing ID

Auto-generation only triggers when `id` is **omitted**. Pass `id` explicitly to preserve identity:

```python
# New entity — auto-generated ID
new_order = Order(customer_id=customer_id, total=1000)

# Reconstitution — preserved ID
loaded_order = Order(id=existing_id, customer_id=customer_id, total=1000)
```

## The Runtime Type Guard

If a generator produces a type that doesn't match `TId`, a `DomainError` (from `pydomain.ddd.exceptions`) is raised at construction time:

```python
# Uuid7Generator returns UUID, but Product expects int
class Product(Entity[int]):
    name: str

# This raises DomainError at construction:
product = Product(name="Widget")
# DomainError: Uuid7Generator produced UUID, but Product expects int
```

This prevents subtle type mismatches from propagating through the system.

## See Also

- [Entity Identity concept](../../concepts/ddd/entity-identity.md)
- [Define an Entity how-to](define-entity.md)
- [Factories concept](../../concepts/ddd/factories.md)
