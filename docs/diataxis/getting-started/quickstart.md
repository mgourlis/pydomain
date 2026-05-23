# Quickstart — Your First Aggregate

This tutorial walks you through building a small domain model with pydomain in about 5 minutes. You'll create a Value Object, an Entity, an Aggregate Root with a Domain Event, and a Repository — the core of Level 1 (Tactical DDD).

No database or infrastructure required — we'll use the built-in testing fakes.

## Step 1: Define a Value Object

Value Objects are immutable and defined by their attributes. Two value objects with the same attributes are equal.

```python
from uuid import UUID
from pydomain.ddd.value_object import ValueObject


class Money(ValueObject):
    """An amount of money in a specific currency."""
    amount: int
    currency: str

    def add(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise ValueError("Cannot add different currencies")
        return self.model_copy(update={"amount": self.amount + other.amount})
```

Key points:
- Inherits from `ValueObject` (frozen Pydantic model — immutable after creation)
- No `id` field — equality is structural (same attributes = equal)
- `model_copy(update=...)` returns a new instance instead of mutating

## Step 2: Define a Domain Event

Domain Events represent facts that happened in the domain. They are named in past tense.

```python
from pydomain.ddd.domain_event import DomainEvent


class OrderPlaced(DomainEvent):
    """Recorded when a customer places an order."""
    order_id: UUID
    total_amount: int
    currency: str
```

Key points:
- Inherits from `DomainEvent` (frozen Pydantic model)
- Named in past tense (`OrderPlaced`, not `PlaceOrder`)
- Automatically gets `event_id` (UUIDv7), `occurred_at` (UTC), `event_version`
- Carries business intent, not entire entity state

## Step 3: Define an Aggregate Root

Aggregate Roots are consistency boundaries. They own domain events and enforce invariants.

```python
from pydomain.ddd.aggregate_root import AggregateRoot


class Order(AggregateRoot[UUID]):
    """An order aggregate — the consistency boundary for order operations."""
    customer_id: UUID
    total: Money
    status: str = "pending"

    def place(self) -> None:
        """Place the order — transitions from pending to placed."""
        if self.status != "pending":
            raise ValueError("Order is not pending")

        self.status = "placed"
        self._add_event(OrderPlaced(
            order_id=self.id,
            total_amount=self.total.amount,
            currency=self.total.currency,
        ))
```

Key points:
- `AggregateRoot[UUID]` — generic over the identity type
- Auto-generates `id` (UUIDv7) when omitted at construction
- `_add_event()` records a domain event in the internal buffer
- Invariants hold after every mutation — `place()` checks status first

## Step 4: Create and Use the Aggregate

```python
from uuid import uuid4

# Create an order — id is auto-generated
order = Order(
    customer_id=uuid4(),
    total=Money(amount=1000, currency="EUR"),
)

# Place it — this records an OrderPlaced event
order.place()

# Inspect the recorded events
events = order.pull_events()
print(f"Event: {type(events[0]).__name__}")  # OrderPlaced
print(f"Status: {order.status}")              # placed
```

## Step 5: Use a Fake Repository for Testing

pydomain ships with testing fakes so you can test domain logic without a database.

```python
from pydomain.testing.fake_repository import FakeRepository

# Create a fake repository
repo: FakeRepository[Order, UUID] = FakeRepository()

# Save the order
await repo.save(order)

# Retrieve it
found = await repo.get_by_id(order.id)
assert found is not None
assert found.id == order.id
assert found.status == "placed"
```

## What You Learned

| Concept | Class | Key Idea |
|---------|-------|----------|
| Value Object | `ValueObject` | Immutable, defined by attributes |
| Domain Event | `DomainEvent` | Immutable fact, past-tense name |
| Aggregate Root | `AggregateRoot[TId]` | Consistency boundary, owns events |
| Repository | `Repository[T, TId]` | Persistence contract for aggregates |

## Where to Go Next

- **[Entities →](../concepts/ddd/entities.md)** — understand identity vs attributes
- **[Aggregates →](../concepts/ddd/aggregates.md)** — deep dive into consistency boundaries
- **[Define an Entity how-to →](../how-to/ddd/define-entity.md)** — step-by-step guide with validation
