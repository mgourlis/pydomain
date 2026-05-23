# How to Define an Aggregate Root

> **Prerequisites:** [Aggregates concept](../../concepts/ddd/aggregates.md), [Entities](../../concepts/ddd/entities.md), [Domain Events](../../concepts/ddd/domain-events.md)

## Problem

You need a consistency boundary — a domain object that enforces invariants and records domain events.

## Solution

Subclass `AggregateRoot[TId]`, add domain fields, and implement mutation methods that check invariants and record events:

```python
from uuid import UUID
from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.exceptions import DomainError


class OrderNotSubmittable(DomainError):
    """Raised when an order cannot be submitted."""


class OrderSubmitted(DomainEvent):
    order_id: UUID
    customer_id: UUID
    total_amount: int


class Order(AggregateRoot[UUID]):
    customer_id: UUID
    total_amount: int
    status: str = "draft"

    def submit(self) -> None:
        if self.status != "draft":
            raise OrderNotSubmittable(
                f"Cannot submit order in '{self.status}' status"
            )
        if self.total_amount <= 0:
            raise OrderNotSubmittable("Order total must be positive")

        self.status = "submitted"
        self._add_event(OrderSubmitted(
            order_id=self.id,
            customer_id=self.customer_id,
            total_amount=self.total_amount,
        ))
```

## Steps

### 1. Define your domain events

Events are named in past tense and carry business intent:

```python
from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.exceptions import DomainError


class OrderNotSubmittable(DomainError):
    """Raised when an order cannot be submitted."""


class OrderNotCancellable(DomainError):
    """Raised when an order cannot be cancelled."""


class OrderSubmitted(DomainEvent):
    order_id: UUID
    customer_id: UUID
    total_amount: int


class OrderCancelled(DomainEvent):
    order_id: UUID
    reason: str
```

### 2. Define the aggregate root

```python
from pydomain.ddd.aggregate_root import AggregateRoot


class Order(AggregateRoot[UUID]):
    customer_id: UUID
    total_amount: int
    status: str = "draft"
    cancellation_reason: str | None = None
```

### 3. Implement mutation methods

Each method:
1. **Checks preconditions** (invariants)
2. **Mutates state**
3. **Records a domain event** if the change is significant

```python
class Order(AggregateRoot[UUID]):
    customer_id: UUID
    total_amount: int
    status: str = "draft"
    cancellation_reason: str | None = None

    def submit(self) -> None:
        """Submit the order for processing."""
        if self.status != "draft":
            raise OrderNotSubmittable(
                f"Cannot submit order in '{self.status}' status"
            )
        if self.total_amount <= 0:
            raise OrderNotSubmittable("Order total must be positive")

        self.status = "submitted"
        self._add_event(OrderSubmitted(
            order_id=self.id,
            customer_id=self.customer_id,
            total_amount=self.total_amount,
        ))

    def cancel(self, reason: str) -> None:
        """Cancel the order."""
        if self.status not in ("draft", "submitted"):
            raise OrderNotCancellable(
                f"Cannot cancel order in '{self.status}' status"
            )
        if not reason.strip():
            raise OrderNotCancellable("Cancellation reason is required")

        self.status = "cancelled"
        self.cancellation_reason = reason
        self._add_event(OrderCancelled(
            order_id=self.id,
            reason=reason,
        ))

    def update_total(self, new_total: int) -> None:
        """Update the order total (only in draft)."""
        if self.status != "draft":
            raise OrderNotSubmittable("Can only update total on draft orders")
        if new_total <= 0:
            raise OrderNotSubmittable("Total must be positive")
        self.total_amount = new_total
        # No event — internal adjustment, not a significant domain event
```

### 4. Use the aggregate

```python
from uuid import uuid4

# Create — id is auto-generated
order = Order(customer_id=uuid4(), total_amount=5000)
print(order.status)   # "draft"
print(order.version)  # 0

# Submit — changes status and records event
order.submit()
print(order.status)   # "submitted"

# Inspect events
events = order.pull_events()
print(len(events))               # 1
print(type(events[0]).__name__)  # "OrderSubmitted"

# Second call returns empty — buffer was drained
assert order.pull_events() == []
```

## Aggregate with Child Value Objects

The aggregate root manages child value objects through its own methods:

```python
from pydomain.ddd.exceptions import DomainError


class OrderNotModifiable(DomainError):
    """Raised when a non-draft order cannot be modified."""


class OrderItem(ValueObject):
    product_name: str
    quantity: int
    unit_price: int


class Order(AggregateRoot[UUID]):
    customer_id: UUID
    items: list[OrderItem] = []
    status: str = "draft"

    @property
    def total(self) -> int:
        return sum(item.quantity * item.unit_price for item in self.items)

    def add_item(self, name: str, quantity: int, price: int) -> None:
        if self.status != "draft":
            raise OrderNotModifiable("Cannot modify a non-draft order")
        item = OrderItem(product_name=name, quantity=quantity, unit_price=price)
        self.items.append(item)
```

External code never creates or manages `OrderItem` directly — it goes through `Order.add_item()`.

## Aggregate with Nested Entities

Child **entities** (objects with their own identity) are also supported. The aggregate root is the consistency boundary — child entities enforce their own local invariants, but the root enforces cross-entity rules.

### Single level

```python
from uuid import UUID
from pydomain.ddd.entity import Entity
from pydomain.ddd.exceptions import DomainError


class OrderNotModifiable(DomainError):
    """Raised when a non-draft order cannot be modified."""


class OrderLine(Entity[UUID]):
    product_name: str
    quantity: int
    unit_price: int

    def change_quantity(self, new_qty: int) -> None:
        if new_qty <= 0:
            raise ValueError("Quantity must be positive")
        self.quantity = new_qty


class Order(AggregateRoot[UUID]):
    customer_id: UUID
    lines: list[OrderLine] = []
    status: str = "draft"

    def add_line(self, name: str, quantity: int, price: int) -> None:
        if self.status != "draft":
            raise OrderNotModifiable("Cannot modify a non-draft order")
        line = OrderLine(product_name=name, quantity=quantity, unit_price=price)
        self.lines.append(line)

    def change_line_quantity(self, line_id: UUID, new_qty: int) -> None:
        """Route mutation through the root — it finds the child and delegates."""
        line = self._find_line(line_id)
        line.change_quantity(new_qty)

    def _find_line(self, line_id: UUID) -> OrderLine:
        for line in self.lines:
            if line.id == line_id:
                return line
        raise ValueError(f"Line {line_id} not found")
```

### Multi-level nesting

There is no depth limit. A child entity can itself contain nested entities:

```python
from pydomain.ddd.exceptions import DomainError


class OrderNotModifiable(DomainError):
    """Raised when a non-draft order cannot be modified."""


class Adjustment(Entity[UUID]):
    reason: str
    amount_delta: int

    def amend(self, new_delta: int, reason: str) -> None:
        if new_delta == 0:
            raise ValueError("Adjustment delta cannot be zero")
        self.amount_delta = new_delta
        self.reason = reason


class OrderLine(Entity[UUID]):
    product_name: str
    quantity: int
    unit_price: int
    adjustments: list[Adjustment] = []

    def adjust(self, reason: str, delta: int) -> None:
        adj = Adjustment(reason=reason, amount_delta=delta)
        self.adjustments.append(adj)

    def amend_adjustment(self, adj_id: UUID, new_delta: int, reason: str) -> None:
        for adj in self.adjustments:
            if adj.id == adj_id:
                adj.amend(new_delta, reason)
                return
        raise ValueError(f"Adjustment {adj_id} not found")


class Order(AggregateRoot[UUID]):
    customer_id: UUID
    lines: list[OrderLine] = []
    status: str = "draft"

    def add_line(self, name: str, quantity: int, price: int) -> None:
        if self.status != "draft":
            raise OrderNotModifiable("Cannot modify a non-draft order")
        line = OrderLine(product_name=name, quantity=quantity, unit_price=price)
        self.lines.append(line)

    def adjust_line(self, line_id: UUID, reason: str, delta: int) -> None:
        line = self._find_line(line_id)
        line.adjust(reason=reason, delta=delta)

    def amend_line_adjustment(
        self, line_id: UUID, adj_id: UUID, new_delta: int, reason: str
    ) -> None:
        """Route a deep mutation through the root."""
        line = self._find_line(line_id)
        line.amend_adjustment(adj_id, new_delta, reason)

    def _find_line(self, line_id: UUID) -> OrderLine:
        for line in self.lines:
            if line.id == line_id:
                return line
        raise ValueError(f"Line {line_id} not found")
```

**Structure:**

```
Order (AggregateRoot)
 └── OrderLine (Entity)        ← level 1
      └── Adjustment (Entity)  ← level 2
```

### Rules for nested entities

| Rule | Why |
|------|-----|
| Only the aggregate root gets a repository | The root is the consistency boundary |
| All mutations route through the root | External callers never hold references to child entities directly |
| Child entities can enforce local invariants | `Adjustment.amend()` validates its own rules |
| Cross-entity invariants belong on the root | The root checks status before delegating to children |
| No depth limit | Pydantic validates nested `BaseModel` instances at any depth |

## Validation Strategy

Entities and aggregates use a **three-tier validation approach**:

### Tier 1: Pydantic validators — structural/format constraints

Rules that are **always true**, regardless of business state. They run at construction time and guarantee the object is structurally sound:

```python
from pydantic import field_validator
from pydomain.ddd.entity import Entity


class Order(AggregateRoot[UUID]):
    customer_id: UUID
    total_amount: int

    @field_validator("total_amount")
    @classmethod
    def total_must_be_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("total_amount cannot be negative")
        return v
```

Use for: email format, non-empty strings, range constraints on individual fields.

### Tier 2: Domain exceptions — business rule violations

Rules that **depend on state** or carry **business meaning**. Raised inside mutation methods:

```python
from pydomain.ddd.exceptions import DomainError


class OrderNotSubmittable(DomainError):
    """Business rule: only draft orders can be submitted."""


class Order(AggregateRoot[UUID]):
    status: str = "draft"

    def submit(self) -> None:
        if self.status != "draft":
            raise OrderNotSubmittable(
                f"Cannot submit order in '{self.status}' status"
            )
        self.status = "submitted"
```

Domain exceptions are **named in the Ubiquitous Language** — they carry business meaning and can be caught/handled at the application layer (e.g., mapped to HTTP 409 Conflict).

### Tier 3: Specifications — reusable, composable rules

Business rules that need to be **shared across contexts** (validation, querying, generation):

```python
from pydomain.ddd.specification import Specification


class DraftOrderSpec(Specification):
    def is_satisfied_by(self, obj: Any) -> bool:
        return isinstance(obj, Order) and obj.status == "draft"


# Use in a mutation method
spec = DraftOrderSpec()
if not spec.is_satisfied_by(order):
    raise OrderNotSubmittable("Only draft orders can be submitted")
```

### When to use which

| Rule Kind | Mechanism | Example | When it Runs |
|-----------|-----------|---------|-------------|
| Field always invalid | `@field_validator` | Email without `@` | Construction time |
| State-dependent rule | `DomainError` subclass | "Only draft orders can submit" | Mutation method |
| Reusable / composable rule | `Specification` + `DomainError` | Draft check in 3 places | Any context |
| Concurrency conflict | `ConcurrencyError` | Version mismatch on save | Repository |

> **Rule of thumb:** If the rule would make sense in a Pydantic model without business context, use a validator. If the rule is about *when* something can happen (state transitions, authorization, business policies), use a `DomainError`.

## Testing

```python
import pytest


def test_submit_order():
    order = Order(customer_id=uuid4(), total_amount=5000)
    order.submit()
    assert order.status == "submitted"
    events = order.pull_events()
    assert len(events) == 1
    assert isinstance(events[0], OrderSubmitted)

def test_submit_non_draft_raises():
    order = Order(customer_id=uuid4(), total_amount=5000)
    order.submit()
    with pytest.raises(OrderNotSubmittable, match="Cannot submit"):
        order.submit()  # Already submitted

def test_cancel_order():
    order = Order(customer_id=uuid4(), total_amount=5000)
    order.submit()
    order.cancel(reason="Changed my mind")
    assert order.status == "cancelled"
    events = order.pull_events()
    assert isinstance(events[0], OrderCancelled)
```

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Publishing events inside aggregate methods | Use `_add_event()` — events are published by the Unit of Work |
| Skipping invariant checks | Every mutation method must enforce preconditions |
| Creating repositories for child entities | Only the aggregate root gets a repository |
| Allowing external mutation of internal state | All changes go through aggregate methods |
| Using `ValueError` for business rule violations | Use `DomainError` subclasses named in the Ubiquitous Language |

## See Also

- [Aggregates concept](../../concepts/ddd/aggregates.md)
- [Publish a Domain Event how-to](publish-domain-event.md)
- [Implement a Repository how-to](implement-repository.md)
