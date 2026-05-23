# How to Publish a Domain Event

> **Prerequisite:** [Domain Events concept](../../concepts/ddd/domain-events.md)

## Problem

You need to record that something happened in your domain so that other parts of the system can react to it.

## Solution

Call `self._add_event()` inside an aggregate method. The Unit of Work publishes the event after a successful commit.

## Steps

### 1. Define the event

Name it in past tense. Carry business intent, not entire entity state:

```python
from uuid import UUID
from pydomain.ddd.domain_event import DomainEvent


class OrderPlaced(DomainEvent):
    order_id: UUID
    customer_id: UUID
    total_amount: int
    currency: str
```

### 2. Record the event in the aggregate

```python
from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.exceptions import DomainError


class OrderNotPlacable(DomainError):
    """Raised when an order cannot be placed."""


class Order(AggregateRoot[UUID]):
    customer_id: UUID
    total: Money
    status: str = "pending"

    def place(self) -> None:
        if self.status != "pending":
            raise OrderNotPlacable("Order is not pending")

        self.status = "placed"
        self._add_event(OrderPlaced(
            order_id=self.id,
            customer_id=self.customer_id,
            total_amount=self.total.amount,
            currency=self.total.currency,
        ))
```

### 3. Inspect events from tests

```python
order = Order(customer_id=customer_id, total=Money(amount=1000, currency="EUR"))
order.place()

events = order.pull_events()
assert len(events) == 1
assert isinstance(events[0], OrderPlaced)
assert events[0].order_id == order.id
```

### 4. Verify auto-generated fields

Every domain event automatically gets:

```python
event = events[0]
print(event.event_id)       # UUIDv7 — auto-generated
print(event.occurred_at)    # datetime — UTC now
print(event.event_version)  # 1 — default
print(event.correlation_id) # None — stamped by Unit of Work
print(event.causation_id)   # None — stamped by Unit of Work
```

## Recording Multiple Events

A single method can record multiple events:

```python
def place(self) -> None:
    self.status = "placed"
    self._add_event(OrderPlaced(order_id=self.id, ...))
    self._add_event(CustomerNotified(customer_id=self.customer_id, reason="order_placed"))
```

Both events will be returned by the next `pull_events()` call.

## The Full Lifecycle

```
1. Aggregate method: self._add_event(event)
   → Event buffered in _pending_events

2. Unit of Work commit:
   → aggregate.pull_events()  — drains buffer
   → event.stamp(...)         — adds correlation_id, causation_id
   → MessageBus.publish(event) — dispatches to handlers
```

The aggregate never knows about the message bus. It just records facts.

## Events Are Immutable

Events are frozen Pydantic models — they cannot be modified after creation:

```python
event = OrderPlaced(order_id=order.id, ...)
event.order_id = uuid4()  # Raises ValidationError — frozen
```

## See Also

- [Domain Events concept](../../concepts/ddd/domain-events.md)
- [Define an Aggregate how-to](define-aggregate.md)
- [Aggregates concept](../../concepts/ddd/aggregates.md)
