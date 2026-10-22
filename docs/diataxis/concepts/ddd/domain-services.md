# Domain Services

> **Adoption Level:** 1 — Tactical DDD
> **Module:** `pydomain.ddd.domain_service`

## What is a Domain Service?

A **Domain Service** encapsulates business logic that doesn't naturally belong to any single [Entity](entities.md) or [Value Object](value-objects.md). It coordinates operations that span multiple aggregates or requires external dependencies.

Domain Services are:

- **Stateless** — they hold no mutable instance data
- **Named with verbs** from the Ubiquitous Language (e.g., `TransferService`, `PricingService`)
- **In the domain layer** — no infrastructure imports

## The `DomainService` Base Class

```python
class DomainService:
    """Marker base class for domain services."""
    __slots__ = ()
```

`DomainService` is a lightweight architectural marker — it carries no behavior of its own. Its value is signaling: a class inheriting from `DomainService` belongs to the domain layer and has no infrastructure imports.

## When to Use a Domain Service

Use a Domain Service when:

- The operation **spans multiple aggregates** (e.g., transferring money between two accounts)
- The operation requires **injected dependencies** that don't belong to any entity (e.g., a rate provider, pricing engine)
- The logic doesn't fit on any single Entity or Value Object

```python
from pydomain.ddd.domain_service import DomainService


class TransferService(DomainService):
    """Transfers money between two accounts."""

    def transfer(self, source: Account, target: Account, amount: Money) -> None:
        if source.id == target.id:
            raise ValueError("Cannot transfer to the same account")
        source.withdraw(amount)
        target.deposit(amount)
```

### Who Saves the Two Aggregates

The service changes `source` and `target` in memory and saves neither: it has no repository, and a repository belongs to the application layer's transaction. The caller (a command handler) loads both aggregates through the repositories of one [Unit of Work](../cqrs/unit-of-work.md), calls the service, and commits once:

<!-- docs-test: skip -->
```python
async def __call__(self, cmd: Transfer, uow: AccountUoW) -> None:
    source = await uow.accounts.get_by_id(cmd.source_id)
    target = await uow.accounts.get_by_id(cmd.target_id)
    TransferService().transfer(source, target, cmd.amount)
    await uow.accounts.save(source)
    await uow.accounts.save(target)
    # the command bus commits: both saves persist together or neither does
```

If the two saves ran in separate transactions, one could succeed and the other fail, leaving money withdrawn and never deposited. Each aggregate still checks its own version on save, so a concurrent change to either one is a conflict rather than a silent overwrite. When the two changes do not have to be atomic, change one aggregate and let a [domain event](domain-events.md) trigger the other change in its own transaction.

## When NOT to Use a Domain Service

**Prefer standalone functions** when the operation doesn't need a class:

```python
# ✅ Simple function — Pythonic and clear
def calculate_total(items: list[OrderItem]) -> Money:
    return sum(item.price for item in items)

# ❌ Unnecessary class wrapping a function
class TotalCalculator(DomainService):
    def calculate(self, items: list[OrderItem]) -> Money:
        ...
```

Python favors functions over classes when no persistent state is needed. The `DomainService` marker is useful when:

- You want explicit architectural signaling (this belongs to the domain layer)
- The service receives injected dependencies via `__init__`
- Multiple related operations share the same dependencies

**Don't use a Domain Service when the logic naturally fits on an Entity or Value Object.** If `Order.calculate_total()` makes sense, put it there.

## Layer Discipline

Domain Services live in the **domain layer** — they must not import from infrastructure:

```python
# ✅ Domain layer — no infrastructure imports
class PricingService(DomainService):
    def calculate(self, items: list[OrderItem]) -> Money:
        ...

# ❌ Leaking infrastructure into the domain
class PricingService(DomainService):
    def __init__(self, db: SQLAlchemy):  # No!
        ...
```

If a service needs infrastructure (database, HTTP client), the concrete implementation belongs in the infrastructure layer, and the domain layer defines an abstract interface.

## Next Steps

- **[Implement a Domain Service →](../../how-to/ddd/implement-domain-service.md)** — step-by-step guide
- **[Entities →](entities.md)** — when logic belongs on an entity
- **[Value Objects →](value-objects.md)** — when logic belongs on a value object
