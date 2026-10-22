# How to Implement an Integration Event

<!-- docs-test: runnable -->

<!-- docs-test: setup
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.message_broker import MessageBroker
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.testing import InMemoryMessageBroker


class OrderShipped(DomainEvent):
    order_id: UUID
    customer_id: UUID
    carrier: str
    tracking_number: str


class OrderPlaced(DomainEvent):
    order_id: UUID


broker = InMemoryMessageBroker()
message_bus = MessageBus()
order_id, customer_id = uuid4(), uuid4()
domain_event = OrderShipped(
    order_id=order_id, customer_id=customer_id, carrier="DHL", tracking_number="1Z999AA"
).stamp(correlation_id=uuid4(), causation_id=uuid4())
-->

> **Prerequisites:** [Integration Events concept](../../concepts/cqrs/integration-events.md), [Domain Events concept](../../concepts/ddd/domain-events.md)

## Problem

You need to communicate a domain state change to an external system or another bounded context via a message broker.

## Solution

Define an `IntegrationEvent` subclass with primitive-only fields. To publish it reliably, register a translator with the [transactional outbox](../infrastructure/configure-outbound-event-gateway.md): the event is written in the same transaction as your data and delivered until it succeeds. The event-handler approach in this guide is simpler but **best-effort**: the handler runs after the commit, and a crash or a broker error before the publish loses the message.

## Steps

### 1. Define the integration event

```python
from pydomain.cqrs.integration_events import IntegrationEvent


class OrderShippedIntegrationEvent(IntegrationEvent):
    order_id: str       # UUID as string
    customer_id: str    # UUID as string
    shipped_at: str     # ISO 8601
    carrier: str
    tracking_number: str
```

All fields must be strict JSON: str, int, finite float, bool, None, and dict (with str keys) or list holding the same, at any depth. The base class validates this at construction time.

### 2. Create the event handler with tracing

```python
class PublishOrderShippedHandler:
    def __init__(self, broker: MessageBroker) -> None:
        self._broker = broker

    async def __call__(self, event: OrderShipped) -> None:
        integration_event = OrderShippedIntegrationEvent(
            order_id=str(event.order_id),
            customer_id=str(event.customer_id),
            shipped_at=event.occurred_at.isoformat(),
            carrier=event.carrier,
            tracking_number=event.tracking_number,
        )
        # Stamp the integration event with tracing IDs from the
        # domain event — this preserves the trace chain across services
        stamped = integration_event.stamp_from(event)
        await self._broker.publish("order.shipped", stamped)
```

### 3. Register the handler

```python
message_bus.register_event(OrderShipped, PublishOrderShippedHandler(broker))
```

Now whenever an `OrderShipped` domain event is collected after a commit, the integration event is published to the broker.

<!-- docs-test: check
await PublishOrderShippedHandler(broker)(domain_event)
topic, published, _headers = broker.published[0]
assert topic == "order.shipped"
assert published.causation_id == str(domain_event.event_id)
-->

## Domain Event → Integration Event Translation

The event handler acts as a translator:

```
Domain Event (internal)          Integration Event (external)
  OrderShipped                     OrderShippedIntegrationEvent
    event_id: UUID                   event_id: str (UUIDv7 as string)
    occurred_at: datetime            occurred_at: str (ISO 8601)
    order_id: UUID                   order_id: str
    customer_id: UUID                customer_id: str
    carrier: str                     carrier: str
    tracking_number: str             tracking_number: str
```

## Auto-Generated Fields

`event_id` and `occurred_at` are auto-generated. Don't set them manually.
`correlation_id` and `causation_id` default to `None` — set them via `stamp()` before publishing:

```python
event = OrderShippedIntegrationEvent(
    order_id=str(order_id),
    customer_id=str(customer_id),
    shipped_at=datetime.now(UTC).isoformat(),
    carrier="DHL",
    tracking_number="1Z999AA",
)

print(event.event_id)        # "018f4e2a-..." — auto-generated UUIDv7 string
print(event.occurred_at)     # "2026-05-22T10:30:00+00:00" — auto-generated ISO 8601
print(event.correlation_id)  # None — not yet stamped

stamped = event.stamp_from(domain_event)
print(stamped.correlation_id)  # "0195e000-..." — now carries tracing
```

## Primitive-Only Enforcement

The base class validates all fields are primitives at construction time:

```python
from pydantic import ValidationError


class BadEvent(IntegrationEvent):
    order_id: UUID  # Must be str!


class Detailed(IntegrationEvent):
    details: dict[str, Any]


for build in (
    lambda: BadEvent(order_id=uuid4()),  # UUID is not an allowed primitive
    lambda: Detailed(details={"at": datetime.now(UTC)}),  # nested values are checked too
):
    try:
        build()
    except ValidationError as exc:
        print(exc.errors()[0]["msg"])
# Value error, Field 'order_id' has disallowed type 'UUID' at order_id. ...
# Value error, Field 'details' has disallowed type 'datetime' at details['at']. ...
```

<!-- docs-test: check
import pytest

with pytest.raises(ValidationError, match=r"at details\['at'\]"):
    Detailed(details={"at": datetime.now(UTC)})
-->

Convert UUIDs to strings with `str()`, datetimes with `.isoformat()`, tuples and sets to lists, and decimals to strings or integer minor units. Non-`str` dict keys, NaN, infinity, more than 100 levels of nesting and cycles are refused too; the error's path includes dict keys, so keep content out of keys ([ADR-074](../../../adr/ADR-074-outbox-relay-and-registry-honour-their-contracts.md)).

If you publish through the [outbox](../infrastructure/configure-outbound-event-gateway.md), you do not need `stamp_from()`: the relay stamps each event with the tracing IDs of the domain event it came from. The translator must return an instance of exactly the class you registered, or the commit fails with `TypeError`.

## Publishing Multiple Integration Events

One domain event can trigger multiple integration events — stamp each from the same domain event:

```python
class OrderPlacedIntegrationEvent(IntegrationEvent):
    order_id: str


class InventoryReservedIntegrationEvent(IntegrationEvent):
    order_id: str


class PublishOrderEventsHandler:
    def __init__(self, broker: MessageBroker) -> None:
        self._broker = broker

    async def __call__(self, event: OrderPlaced) -> None:
        await self._broker.publish(
            "order.placed",
            OrderPlacedIntegrationEvent(order_id=str(event.order_id)).stamp_from(event),
        )
        await self._broker.publish(
            "inventory.reserved",
            InventoryReservedIntegrationEvent(order_id=str(event.order_id)).stamp_from(
                event
            ),
        )
```

<!-- docs-test: check
placed = OrderPlaced(order_id=uuid4()).stamp(correlation_id=uuid4(), causation_id=uuid4())
await PublishOrderEventsHandler(broker)(placed)
assert [topic for topic, _event, _headers in broker.published[-2:]] == [
    "order.placed",
    "inventory.reserved",
]
-->

## When NOT to Use Integration Events

- **Within the same bounded context** — use domain events (in-process)
- **For query results** — use the Query Bus
- **For commands** — use the Command Bus (or a saga for cross-aggregate coordination)

Integration events are for **cross-boundary** communication only.

## See Also

- [Integration Events concept](../../concepts/cqrs/integration-events.md)
- [Domain Events concept](../../concepts/ddd/domain-events.md)
- [Publish a Domain Event](../ddd/publish-domain-event.md)
- [Publish Integration Events (Recipe)](../recipes/publish-integration-events.md)
