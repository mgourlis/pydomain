# Entity Identity

<!-- docs-test: runnable -->

<!-- docs-test: setup
from typing import ClassVar, NewType
from uuid import UUID, uuid4

from pydomain.ddd.entity import Entity
from pydomain.ddd.id_generator import IdGenerator

customer_id = uuid4()
existing_id = uuid4()
-->

> **Adoption Level:** 1 — Tactical DDD
> **Module:** `pydomain.ddd.id_generator`

## Overview

Every [Entity](entities.md) has a unique identity (`id` field) that distinguishes it from all other entities of the same type. This identity is stable — it never changes, even as the entity's attributes evolve.

pydomain provides a pluggable ID generation system via the `IdGenerator` protocol, with UUIDv7 as the default strategy.

## The `TId` Type Parameter

`Entity[TId]` is generic over the identity type:

```python
class Order(Entity[UUID]): ...      # identity is a UUID
class Product(Entity[int]): ...     # identity is an int (e.g., Snowflake ID)
class Tenant(Entity[str]): ...      # identity is a string (e.g., ULID)
```

The `TId` annotation determines what type the `id` field accepts and what type the auto-generator must produce.

`TId` can also be a `NewType`, a PEP 695 alias, an `Annotated` type or a union of them, nested to any depth.
The type guard checks the generated value against the types they name ([ADR-070](../../../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md)):

```python
OrderId = NewType("OrderId", UUID)
type InvoiceId = UUID


class PurchaseOrder(Entity[OrderId]): ...
class Invoice(Entity[InvoiceId]): ...


assert isinstance(PurchaseOrder().id, UUID)
assert isinstance(Invoice().id, UUID)
```

## Auto-ID Generation

When you create an entity **without specifying `id`**, pydomain auto-generates one:

```python
order = Order(customer_id=customer_id, total=1000)
# order.id is auto-generated
```

The generation flow:

1. The `@model_validator(mode="before")` checks if `id` is missing
2. Calls `cls._id_generator.generate()` to produce a value
3. **Runtime type guard** — verifies the generated value matches `TId`, after resolving `NewType`, aliases,
   `Annotated` and every member of an `Optional` or union, recursively
4. If the type doesn't match, raises `DomainError`. If the guard cannot check the type at all, such as the unbound
   `TypeVar` of a generic entity built without a type argument, it raises `DomainError` too, saying to parametrize
   the generic or pass an id
5. Adds `id` to a copy of the input: a dict passed to `model_validate()` is never changed, so validating it again
   generates a new id

This guard prevents subtle bugs like a Snowflake ID generator producing `int` values for an entity that expects `UUID`.

## The `IdGenerator` Protocol

<!-- docs-test: skip -->
```python
@runtime_checkable
class IdGenerator[TId](Protocol):
    def generate(self) -> TId: ...
```

Any class with a `generate()` method returning the right type structurally conforms. No inheritance required.

## Default: `Uuid7Generator`

<!-- docs-test: skip -->
```python
class Uuid7Generator:
    def generate(self) -> UUID:
        return UUID(int=uuid7().int)
```

UUIDv7 identifiers are:

- **Time-ordered** — monotonically increasing, database-friendly for B-tree indexes
- **Globally unique** — no coordination needed between instances
- **Sortable by creation time** — useful for debugging and log correlation

## Custom ID Generators

Create your own by implementing the `IdGenerator` protocol — explicit inheritance recommended:

```python
class SnowflakeIdGenerator(IdGenerator[int]):
    """Generates Snowflake-style integer IDs."""
    def __init__(self, worker_id: int, epoch: int) -> None:
        self._worker_id = worker_id
        self._epoch = epoch

    def generate(self) -> int:
        # ... Snowflake algorithm ...
        return generated_int
```

Then configure it:

<!-- docs-test: skip -->
```python
from pydomain.ddd.entity import Entity

Entity.configure(id_generator=SnowflakeIdGenerator(worker_id=1, epoch=1700000000))
```

## Configuring the Generator

Call `Entity.configure()` once at application startup:

```python
from pydomain.ddd.entity import Entity
from pydomain.ddd.id_generator import Uuid7Generator

# Once at startup — affects all Entity subclasses
Entity.configure(id_generator=Uuid7Generator())
```

Individual entity subclasses can override by setting `_id_generator` as a class variable:

```python
class Product(Entity[int]):
    _id_generator: ClassVar[IdGenerator[int]] = SnowflakeIdGenerator(worker_id=1, epoch=0)
```

## Reconstitution and Identity

When rebuilding an entity from persisted state (e.g., loading from a database), you **always pass the existing `id`**:

```python
# Reconstitution — preserves existing identity
order = Order(id=existing_id, customer_id=..., total=...)
```

Auto-generation only triggers when `id` is **omitted**. This is why [Factories](factories.md) distinguish between `create()` (new identity) and `reconstitute()` (existing identity).

## Next Steps

- **[Entity Identity how-to →](../../how-to/ddd/entity-identity.md)** — configure custom generators
- **[Entities →](entities.md)** — the identity-bearing domain objects
- **[Factories →](factories.md)** — creation vs reconstitution
