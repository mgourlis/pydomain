# How to Implement a Specification

> **Prerequisite:** [Specifications concept](../../concepts/ddd/specifications.md)

## Problem

You need to encapsulate a business rule as a composable, reusable predicate.

## Solution

Subclass `Specification`, implement `is_satisfied_by()`, and optionally override `subsumes()`:

```python
from pydomain.ddd.specification import Specification


class IsHighValue(Specification):
    threshold: int

    def is_satisfied_by(self, order: Any) -> bool:
        return order.total >= self.threshold
```

## Steps

### 1. Define the specification

```python
from typing import Any
from pydomain.ddd.specification import Specification


class IsEligibleForDiscount(Specification):
    min_total: int
    min_items: int

    def is_satisfied_by(self, order: Any) -> bool:
        return (
            order.total >= self.min_total
            and len(order.items) >= self.min_items
        )
```

### 2. Use it for validation

```python
eligible = IsEligibleForDiscount(min_total=1000, min_items=3)

if eligible.is_satisfied_by(order):
    apply_discount(order)
```

### 3. Compose with other specifications

```python
class IsPremiumCustomer(Specification):
    def is_satisfied_by(self, order: Any) -> bool:
        return order.customer.is_premium

premium = IsPremiumCustomer()
high_value = IsHighValue(threshold=1000)

# AND — both must be satisfied
premium_and_high_value = premium.and_(high_value)

# OR — either must be satisfied
premium_or_high_value = premium.or_(high_value)

# NOT — negation
not_premium = premium.not_()
```

### 4. Implement subsumption

Override `subsumes()` when one specification is a superset of another:

```python
class MinimumAge(Specification):
    minimum: int

    def is_satisfied_by(self, person: Any) -> bool:
        return person.age >= self.minimum

    def subsumes(self, other: Specification) -> bool:
        if not isinstance(other, MinimumAge):
            return False
        return self.minimum <= other.minimum


adult = MinimumAge(minimum=18)
senior = MinimumAge(minimum=65)

# Every senior is an adult — adult subsumes senior
assert adult.subsumes(senior) is True
assert senior.subsumes(adult) is False
```

### 5. Use with repositories

Specifications can drive repository queries when the infrastructure supports it:

```python
class OrderRepository:
    async def find_satisfying(self, spec: Specification) -> list[Order]:
        # Translate the specification into a database query
        ...
```

## Complete Example

```python
from typing import Any
from pydomain.ddd.specification import Specification


class IsInStock(Specification):
    def is_satisfied_by(self, product: Any) -> bool:
        return product.stock_quantity > 0


class IsOnSale(Specification):
    discount_threshold: float

    def is_satisfied_by(self, product: Any) -> bool:
        return product.discount >= self.discount_threshold


class IsCheapEnough(Specification):
    max_price: int

    def is_satisfied_by(self, product: Any) -> bool:
        return product.price <= self.max_price


# Compose: "in stock AND (on sale OR cheap)"
in_stock = IsInStock()
on_sale = IsOnSale(discount_threshold=0.2)
cheap = IsCheapEnough(max_price=1000)

available_deal = in_stock.and_(on_sale.or_(cheap))

# Use it
if available_deal.is_satisfied_by(product):
    add_to_recommendations(product)
```

## Testing

```python
def test_is_high_value():
    spec = IsHighValue(threshold=1000)
    order = Order(total=1500, items=[])
    assert spec.is_satisfied_by(order) is True

def test_is_high_value_below_threshold():
    spec = IsHighValue(threshold=1000)
    order = Order(total=500, items=[])
    assert spec.is_satisfied_by(order) is False

def test_composition_and():
    high_value = IsHighValue(threshold=1000)
    premium = IsPremiumCustomer()
    combined = high_value.and_(premium)

    # Both must be satisfied
    assert combined.is_satisfied_by(high_value_premium_order) is True
    assert combined.is_satisfied_by(high_value_regular_order) is False
```

## See Also

- [Specifications concept](../../concepts/ddd/specifications.md)
- [Repositories concept](../../concepts/ddd/repositories.md)
