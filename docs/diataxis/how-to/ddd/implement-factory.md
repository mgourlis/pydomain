# How to Implement a Factory

> **Prerequisite:** [Factories concept](../../concepts/ddd/factories.md)

## Problem

You need to encapsulate complex creation or reconstitution logic for domain objects.

## Solution

Create a class inheriting from `Factory[T]` (for new objects) or `ReconstitutionFactory[T]` (for rebuilding from persisted state):

```python
from pydomain.ddd.factory import Factory


class OrderFactory(Factory[Order]):  # explicit protocol inheritance
    def create(self, customer_id: UUID, items: list[dict]) -> Order: ...
```

> **Note:** These are `typing.Protocol` classes — structural subtyping also works (any class with a matching `create()` method conforms). Explicit inheritance is recommended for documentation clarity and static type checking.

## Steps

### 1. Simple factory for new objects

```python
from uuid import UUID
from pydomain.ddd.factory import Factory
from pydomain.ddd.exceptions import DomainError


class EmptyOrderError(DomainError):
    """Raised when attempting to create an order without items."""


class OrderFactory(Factory[Order]):
    """Creates new Order aggregates with validated defaults."""

    def create(self, customer_id: UUID, items: list[dict]) -> Order:
        if not items:
            raise EmptyOrderError("Order must have at least one item")

        order_items = [
            OrderItem(product_name=i["name"], quantity=i["qty"], unit_price=i["price"])
            for i in items
        ]
        total = sum(item.quantity * item.unit_price for item in order_items)

        return Order(
            customer_id=customer_id,
            total_amount=total,
            status="draft",
        )
```

### 2. Factory with injected dependencies

```python
class OrderFactory(Factory[Order]):
    def __init__(self, pricing: PricingService, inventory: InventoryChecker) -> None:
        self._pricing = pricing
        self._inventory = inventory

    def create(self, customer_id: UUID, product_ids: list[UUID]) -> Order:
        # Check inventory first
        for pid in product_ids:
            if not self._inventory.is_available(pid):
                raise ProductNotAvailableError(f"Product {pid} is not in stock")

        # Calculate pricing with discounts
        items = [self._pricing.get_order_item(pid) for pid in product_ids]
        total = self._pricing.calculate_total(items, customer_id)

        return Order(customer_id=customer_id, total_amount=total)
```

### 3. Reconstitution factory

For rebuilding domain objects from persisted state — **never generates a new identity**:

```python
from pydomain.ddd.factory import ReconstitutionFactory
from pydomain.ddd.exceptions import DomainError


class OrderNotModifiable(DomainError):
    """Raised when a non-draft order cannot be modified."""


class OrderReconstitutor(ReconstitutionFactory[Order]):
    """Rebuilds Order aggregates from database rows."""

    def reconstitute(self, row: dict) -> Order:
        return Order(
            id=row["id"],              # Preserved identity!
            customer_id=row["customer_id"],
            total_amount=row["total_amount"],
            status=row["status"],
            version=row["version"],
        )
```

> ⚠️ **Critical:** The `reconstitute()` method must always pass the existing `id` from the persisted data. Never let the `IdGenerator` produce a new one during reconstitution.

### 4. Use the factory

```python
# New object
factory = OrderFactory(pricing=pricing_service, inventory=inventory_checker)
order = factory.create(customer_id=customer_id, product_ids=[uuid4(), uuid4()])

# Reconstitution
reconstitutor = OrderReconstitutor()
loaded_order = reconstitutor.reconstitute(db_row)
```

## Factory Method on Aggregate Root

Sometimes the aggregate itself is the best factory for its children:

```python
class Order(AggregateRoot[UUID]):
    items: list[OrderItem] = []

    def add_item(self, product_name: str, quantity: int, price: int) -> None:
        """Factory method — creates and appends an OrderItem."""
        if self.status != "draft":
            raise OrderNotModifiable("Cannot modify a non-draft order")
        item = OrderItem(product_name=product_name, quantity=quantity, unit_price=price)
        self.items.append(item)
```

No separate factory class needed — the aggregate manages its own children.

## See Also

- [Factories concept](../../concepts/ddd/factories.md)
- [Entity Identity](../../concepts/ddd/entity-identity.md) — why reconstitution must preserve identity
- [Repositories concept](../../concepts/ddd/repositories.md) — where reconstitution happens
