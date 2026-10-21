# How to Implement an Integration Event

> **Prerequisites:** [Integration Events concept](../../concepts/cqrs/integration-events.md), [Domain Events concept](../../concepts/ddd/domain-events.md)

## Problem

You need to communicate a domain state change to an external system or another bounded context via a message broker.

## Solution

Define an `IntegrationEvent` subclass with primitive-only fields. Publish it from an event handler that translates a domain event into an integration event.

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
# Raises ValueError — UUID is not an allowed primitive
class BadEvent(IntegrationEvent):
    order_id: UUID  # Must be str!

# Raises ValueError — datetime is not an allowed primitive
class BadEvent(IntegrationEvent):
    shipped_at: datetime  # Must be str!

# Raises ValueError at construction — the check also looks inside dicts and lists:
#   Field 'details' has disallowed type 'datetime' at details['at']
class Detailed(IntegrationEvent):
    details: dict[str, Any]

Detailed(details={"at": datetime.now(UTC)})
```

Convert UUIDs to strings with `str()`, datetimes with `.isoformat()`, tuples and sets to lists, and decimals to strings or integer minor units. Non-`str` dict keys, NaN and infinity are refused too ([ADR-074](../../../adr/ADR-074-outbox-relay-and-registry-honour-their-contracts.md)).

If you publish through the [outbox](../infrastructure/configure-outbound-event-gateway.md), you do not need `stamp_from()`: the relay stamps each event with the tracing IDs of the domain event it came from. The translator must return an instance of exactly the class you registered, or the commit fails with `TypeError`.

## Publishing Multiple Integration Events

One domain event can trigger multiple integration events — stamp each from the same domain event:

```python
class PublishOrderEventsHandler:
    def __init__(self, broker: MessageBroker) -> None:
        self._broker = broker

    async def __call__(self, event: OrderPlaced) -> None:
        await self._broker.publish(
            "order.placed",
            OrderPlacedIntegrationEvent(...).stamp_from(event),
        )
        await self._broker.publish(
            "inventory.reserved",
            InventoryReservedIntegrationEvent(...).stamp_from(event),
        )
```

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
