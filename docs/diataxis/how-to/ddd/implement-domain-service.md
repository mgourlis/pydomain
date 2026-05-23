# How to Implement a Domain Service

> **Prerequisite:** [Domain Services concept](../../concepts/ddd/domain-services.md)

## Problem

You have domain logic that doesn't naturally belong to any single Entity or Value Object — it spans multiple aggregates or requires external dependencies.

## Solution

Create a stateless class inheriting from `DomainService`, or use a standalone function when no class state is needed.

## Steps

### 1. Prefer a standalone function first

If the operation has no dependencies and no shared state, a function is clearer:

```python
def calculate_order_total(items: list[OrderItem]) -> Money:
    """Calculate total for a list of order items."""
    total_amount = sum(item.quantity * item.unit_price for item in items)
    return Money(amount=total_amount, currency="EUR")
```

### 2. Use a Domain Service for multi-aggregate coordination

When the operation spans multiple aggregates:

```python
from pydomain.ddd.domain_service import DomainService


class TransferService(DomainService):
    """Transfers money between two accounts."""

    def transfer(self, source: Account, target: Account, amount: Money) -> None:
        if source.id == target.id:
            raise ValueError("Cannot transfer to the same account")
        if not source.can_withdraw(amount):
            raise ValueError("Insufficient funds")

        source.withdraw(amount)
        target.deposit(amount)
```

### 3. Use a Domain Service for injected dependencies

When the operation needs external services:

```python
class PricingService(DomainService):
    """Calculates pricing with customer-specific discounts."""

    def __init__(self, rate_provider: RateProvider) -> None:
        self._rate_provider = rate_provider

    def calculate(self, items: list[OrderItem], customer_tier: str) -> Money:
        base_total = sum(item.quantity * item.unit_price for item in items)
        discount = self._rate_provider.get_discount(customer_tier)
        discounted = int(base_total * (1 - discount))
        return Money(amount=discounted, currency="EUR")
```

### 4. Keep it in the domain layer

Domain Services must not import infrastructure:

```python
# ✅ Correct — domain layer only
class PricingService(DomainService):
    def __init__(self, rate_provider: RateProvider) -> None:
        self._rate_provider = rate_provider

# ❌ Wrong — infrastructure leak
class PricingService(DomainService):
    def __init__(self, db: AsyncSession) -> None:  # No SQLAlchemy in domain!
        ...
```

If you need infrastructure, define an abstract interface (Protocol) in the domain layer and implement it in infrastructure.

## When to Use What

| Pattern | Use When |
|---------|----------|
| **Standalone function** | No dependencies, no class state, single operation |
| **Domain Service class** | Multi-aggregate coordination, injected dependencies, architectural signaling |
| **Entity method** | Logic belongs to a single entity |
| **Value Object method** | Pure computation on immutable data |

## See Also

- [Domain Services concept](../../concepts/ddd/domain-services.md)
- [Entities concept](../../concepts/ddd/entities.md) — when logic belongs on an entity
- [Value Objects concept](../../concepts/ddd/value-objects.md) — when logic belongs on a value object
