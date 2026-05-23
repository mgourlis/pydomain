I’ve updated the Recipe to replace generic `ValueError`s in the Aggregate Root with custom `DomainError` subclasses, matching the exception pattern from the “How to Define an Aggregate Root” guide. The field-level validators in the Value Objects still use `ValueError` (as they are structural constraints), while the business rule violations in the aggregate now use explicit, Ubiquitous Language exception types. The tests are adjusted accordingly.

Here’s the revised Recipe:

---

# Recipe: Build a DDD-Only Application

> **Adoption Level:** 1 — Tactical DDD
> **Prerequisites:** All Phase 1 concepts and how-tos

This recipe shows how to build a complete DDD-only application using pydomain's Level 1 building blocks. No CQRS, no Event Sourcing — just a rich domain model with entities, value objects, aggregates, and repositories.

## What You'll Build

A simple **Order Management** domain with:

- `Money` value object — immutable currency amounts
- `OrderItem` value object — line items in an order
- `Order` aggregate root — enforces business rules, records events
- `OrderPlaced` domain event — records when an order is placed
- `InMemoryOrderRepository` — persistence (in-memory for this recipe)

## Step 1: Value Objects

```python
# domain/value_objects.py
from pydantic import field_validator
from pydomain.ddd.value_object import ValueObject


class Money(ValueObject):
    amount: int
    currency: str

    @field_validator("amount")
    @classmethod
    def amount_must_be_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Amount cannot be negative")
        return v

    def add(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise ValueError(f"Cannot add {other.currency} to {self.currency}")
        return self.model_copy(update={"amount": self.amount + other.amount})


class OrderItem(ValueObject):
    product_name: str
    quantity: int
    unit_price: int

    @field_validator("quantity")
    @classmethod
    def quantity_must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Quantity must be positive")
        return v

    @property
    def subtotal(self) -> Money:
        return Money(amount=self.quantity * self.unit_price, currency="EUR")
```

## Step 2: Domain Event

```python
# domain/events.py
from uuid import UUID
from pydomain.ddd.domain_event import DomainEvent


class OrderPlaced(DomainEvent):
    order_id: UUID
    customer_id: UUID
    total_amount: int
    item_count: int


class OrderCancelled(DomainEvent):
    order_id: UUID
    reason: str


class OrderDeleted(DomainEvent):
    order_id: UUID
    reason: str
```

## Step 3: Aggregate Root

```python
# domain/aggregates.py
from uuid import UUID
from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.exceptions import DomainError
from domain.events import OrderPlaced, OrderCancelled, OrderDeleted
from domain.value_objects import OrderItem, Money


class OrderNotModifiable(DomainError):
    """Raised when attempting to modify a non-draft order."""

class OrderNotPlaceable(DomainError):
    """Raised when an order cannot be placed."""

class OrderNotCancellable(DomainError):
    """Raised when an order cannot be cancelled."""

class OrderNotDeletable(DomainError):
    """Raised when an order cannot be deleted."""


class Order(AggregateRoot[UUID]):
    customer_id: UUID
    items: list[OrderItem] = []
    status: str = "draft"
    cancellation_reason: str | None = None

    @property
    def total(self) -> Money:
        if not self.items:
            return Money(amount=0, currency="EUR")
        return self.items[0].subtotal.add(
            *[item.subtotal for item in self.items[1:]]
        )

    def add_item(self, name: str, quantity: int, unit_price: int) -> None:
        if self.status != "draft":
            raise OrderNotModifiable("Cannot modify a non-draft order")
        item = OrderItem(product_name=name, quantity=quantity, unit_price=unit_price)
        self.items.append(item)

    def place(self) -> None:
        if self.status != "draft":
            raise OrderNotPlaceable(
                f"Cannot place order in '{self.status}' status"
            )
        if not self.items:
            raise OrderNotPlaceable("Cannot place an empty order")

        self.status = "placed"
        self._add_event(OrderPlaced(
            order_id=self.id,
            customer_id=self.customer_id,
            total_amount=self.total.amount,
            item_count=len(self.items),
        ))

    def cancel(self, reason: str) -> None:
        if self.status not in ("draft", "placed"):
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

    def delete(self, reason: str) -> None:
        if self.status not in ("draft", "cancelled"):
            raise OrderNotDeletable(
                f"Cannot delete order in '{self.status}' status"
            )
        if not reason.strip():
            raise OrderNotDeletable("Deletion reason is required")

        self.status = "deleted"
        self._add_event(OrderDeleted(
            order_id=self.id,
            reason=reason,
        ))
```

## Step 4: Repository (In-Memory)

```python
# infrastructure/repository.py
from uuid import UUID
from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.exceptions import ConcurrencyError
from pydomain.ddd.repository import Repository


class InMemoryOrderRepository(Repository[Order, UUID]):
    def __init__(self) -> None:
        self._store: dict[UUID, Order] = {}
        self._seen: list[Order] = []

    async def save(self, aggregate: Order, command_id: UUID | None = None) -> None:
        existing = self._store.get(aggregate.id)
        if existing is not None and existing.version != aggregate.version:
            raise ConcurrencyError("Version mismatch")
        self._store[aggregate.id] = aggregate
        self._seen.append(aggregate)

    async def get_by_id(self, id_: UUID) -> Order | None:
        found = self._store.get(id_)
        if found is not None:
            self._seen.append(found)
        return found

    async def delete(self, id_: UUID) -> None:
        aggregate = self._store.pop(id_, None)
        if aggregate is not None:
            self._seen.append(aggregate)  # Track for event collection

    def pull_events(self) -> list[DomainEvent]:
        events: list[DomainEvent] = []
        for aggregate in self._seen:
            events.extend(aggregate.pull_events())
        self._seen.clear()
        return events
```

## Step 5: Tests

```python
# tests/test_domain.py
import pytest
from uuid import uuid4
from domain.value_objects import Money
from domain.aggregates import (
    Order,
    OrderNotModifiable,
    OrderNotPlaceable,
    OrderNotCancellable,
    OrderNotDeletable,
)
from domain.events import OrderPlaced, OrderCancelled, OrderDeleted


class TestMoney:
    def test_add_same_currency(self):
        a = Money(amount=100, currency="EUR")
        b = Money(amount=50, currency="EUR")
        result = a.add(b)
        assert result == Money(amount=150, currency="EUR")

    def test_add_different_currency_raises(self):
        a = Money(amount=100, currency="EUR")
        b = Money(amount=50, currency="USD")
        with pytest.raises(ValueError):
            a.add(b)


class TestOrder:
    def test_place_records_event(self):
        order = Order(customer_id=uuid4())
        order.add_item("Widget", quantity=2, unit_price=500)
        order.place()

        assert order.status == "placed"
        events = order.pull_events()
        assert len(events) == 1
        assert isinstance(events[0], OrderPlaced)
        assert events[0].total_amount == 1000
        assert events[0].item_count == 1

    def test_place_empty_order_raises(self):
        order = Order(customer_id=uuid4())
        with pytest.raises(OrderNotPlaceable, match="empty order"):
            order.place()

    def test_cancel_placed_order(self):
        order = Order(customer_id=uuid4())
        order.add_item("Widget", quantity=1, unit_price=100)
        order.place()
        order.cancel(reason="Changed my mind")

        assert order.status == "cancelled"
        events = order.pull_events()
        assert isinstance(events[0], OrderCancelled)

    def test_add_item_to_placed_order_raises(self):
        order = Order(customer_id=uuid4())
        order.add_item("Widget", quantity=1, unit_price=100)
        order.place()
        with pytest.raises(OrderNotModifiable, match="non-draft"):
            order.add_item("Gadget", quantity=1, unit_price=200)

    def test_order_total(self):
        order = Order(customer_id=uuid4())
        order.add_item("Widget", quantity=2, unit_price=500)
        order.add_item("Gadget", quantity=1, unit_price=300)
        assert order.total == Money(amount=1300, currency="EUR")

    def test_delete_draft_order(self):
        order = Order(customer_id=uuid4())
        order.add_item("Widget", quantity=1, unit_price=100)
        order.delete(reason="No longer needed")

        assert order.status == "deleted"
        events = order.pull_events()
        assert isinstance(events[0], OrderDeleted)
        assert events[0].reason == "No longer needed"

    def test_delete_placed_order_raises(self):
        order = Order(customer_id=uuid4())
        order.add_item("Widget", quantity=1, unit_price=100)
        order.place()
        with pytest.raises(OrderNotDeletable, match="placed"):
            order.delete(reason="Don't want it")


class TestOrderRepository:
    async def test_save_and_retrieve(self):
        repo = InMemoryOrderRepository()
        order = Order(customer_id=uuid4())
        order.add_item("Widget", quantity=1, unit_price=100)

        await repo.save(order)
        found = await repo.get_by_id(order.id)

        assert found is not None
        assert found.id == order.id

    async def test_delete(self):
        repo = InMemoryOrderRepository()
        order = Order(customer_id=uuid4())
        await repo.save(order)
        await repo.delete(order.id)
        assert await repo.get_by_id(order.id) is None

    async def test_delete_collects_events(self):
        repo = InMemoryOrderRepository()
        order = Order(customer_id=uuid4())
        order.add_item("Widget", quantity=1, unit_price=100)
        await repo.save(order)

        # Load, mutate, delete — events must be collected
        loaded = await repo.get_by_id(order.id)
        loaded.delete(reason="No longer needed")
        await repo.delete(order.id)

        events = repo.pull_events()
        assert len(events) == 1
        assert isinstance(events[0], OrderDeleted)
        assert events[0].order_id == order.id
```

## Step 6: Application Entry Point

```python
# main.py
import asyncio
from uuid import uuid4


async def main() -> None:
    repo = InMemoryOrderRepository()

    # Create an order
    customer_id = uuid4()
    order = Order(customer_id=customer_id)
    order.add_item("Widget", quantity=2, unit_price=500)
    order.add_item("Gadget", quantity=1, unit_price=300)

    # Place it
    order.place()
    print(f"Order {order.id} placed — status: {order.status}")
    print(f"Total: {order.total.amount / 100:.2f} {order.total.currency}")

    # Save
    await repo.save(order)
    print("Order saved.")

    # Load and verify
    loaded = await repo.get_by_id(order.id)
    assert loaded is not None
    print(f"Loaded order status: {loaded.status}")

    # Collect events
    events = repo.pull_events()
    for event in events:
        print(f"Event: {type(event).__name__} (id={event.event_id})")


if __name__ == "__main__":
    asyncio.run(main())
```

## What's Next?

You now have a working DDD-only application. When you're ready to add:

- **Commands and Queries** → Move to [Level 2: CQRS](../../concepts/cqrs/commands.md)
- **Event-driven side effects** → Move to [Level 3: Message Bus](../../concepts/infrastructure/message-bus.md)
- **Full audit trail** → Move to [Level 4: Event Sourcing](../../concepts/es/event-sourcing.md)

Each level builds on what you already have — no rewriting required.
