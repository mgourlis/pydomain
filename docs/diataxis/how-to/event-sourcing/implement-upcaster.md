# How to Implement an Upcaster

<!-- docs-test: runnable -->

> **Adoption Level:** 4 · Prerequisites: [Event Versioning concept](../../concepts/es/event-versioning.md), [Domain Events concept](../../concepts/ddd/domain-events.md)

This guide shows you how to define upcasters to migrate events across schema versions when your event payloads change.

## 1. Understand the version step

Each upcaster handles exactly one version step for one event type. For example, if `OrderPlaced` evolved v1 → v2 → v3, you need two upcasters:

```
OrderPlaced v1 ──[V1ToV2]──▶ OrderPlaced v2 ──[V2ToV3]──▶ OrderPlaced v3
```

## 2. Define the upcaster

Subclass `EventUpcaster` and declare `source_type`, `source_version`, `target_version` as `ClassVar`:

```python
from typing import Any, ClassVar
from pydomain.es.upcasting import EventUpcaster


class OrderPlacedV1ToV2(EventUpcaster):
    source_type: ClassVar[str] = "OrderPlaced"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2

    def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
        # v1: {"total": 1000}
        # v2: {"total_amount": 1000, "currency": "EUR"}
        event["total_amount"] = event.pop("total")
        event["currency"] = "EUR"
        return event


class OrderPlacedV2ToV3(EventUpcaster):
    source_type: ClassVar[str] = "OrderPlaced"
    source_version: ClassVar[int] = 2
    target_version: ClassVar[int] = 3

    def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
        # v3 added "channel"; v2 orders all came from the web shop
        event.setdefault("channel", "web")
        return event
```

The `upcast()` method wraps `_transform` — if it raises, the exception becomes an `UpcastError` with diagnostic context.

Change only the fields. Do not set `event_version` in `_transform`: the `EventRegistry` writes the version the chain reached (step 4).

## 3. Register upcasters

Add them to the `UpcasterRegistry`, one per version step:

```python
from pydomain.es.upcasting import UpcasterRegistry


registry = UpcasterRegistry()
registry.register(OrderPlacedV1ToV2)
registry.register(OrderPlacedV2ToV3)
```

`register` raises `TypeError` if an upcaster lacks `source_type`, `source_version` or `target_version`, and `ValueError` if `target_version` is not greater than `source_version` or the step already has an upcaster.

## 4. Attach the registry to the `EventRegistry`

The `EventRegistry` applies the chain when it deserializes a stored event. Register the current class, at the version the last upcaster reaches:

```python
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.event_registry import EventRegistry


class OrderPlaced(DomainEvent):
    event_version: int = 3
    order_id: str
    total_amount: int
    currency: str
    channel: str


events = EventRegistry(upcaster_registry=registry)
events.register(OrderPlaced)

stored = {
    "type": "OrderPlaced",
    "version": 1,
    "data": {"event_version": 1, "order_id": "ORD-1", "total": 1000},
}
event = events.deserialize(stored)

assert event.event_version == 3
assert (event.total_amount, event.currency, event.channel) == (1000, "EUR", "web")
```

`deserialize` resolves the chain for the stored version (`registry.resolve("OrderPlaced", 1)` returns `[OrderPlacedV1ToV2, OrderPlacedV2ToV3]`), applies it, writes `event_version=3`, then validates. An event stored at v3 resolves an empty chain and is validated as it is.

## 5. Check that the chain is complete

`deserialize` raises `UpcastError` when the chain does not end at the class's version. That catches a missing upcaster on the first read, instead of validating an old payload with defaults. Test every stored version you still have:

```python
from pydomain.es.exceptions import UpcastError


stored_shapes = {
    1: {"order_id": "ORD-1", "total": 1000},
    2: {"order_id": "ORD-1", "total_amount": 1000, "currency": "EUR"},
    3: {"order_id": "ORD-1", "total_amount": 1000, "currency": "EUR", "channel": "web"},
}
for version, payload in stored_shapes.items():
    envelope = {"type": "OrderPlaced", "version": version, "data": payload}
    assert events.deserialize(envelope).event_version == 3

incomplete = UpcasterRegistry()
incomplete.register(OrderPlacedV1ToV2)
gappy = EventRegistry(upcaster_registry=incomplete)
gappy.register(OrderPlaced)
try:
    gappy.deserialize(stored)
except UpcastError as exc:
    assert "ends at v2, but the class is at v3" in str(exc)
else:
    raise AssertionError("the v2 payload was validated as v3")
```

Then check the round trip: an event read, written back and read again must come back equal, which proves no upcaster runs twice:

```python
assert events.deserialize(events.serialize(event)) == event
```

## 6. Common transformations

### Rename a field

```python
def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
    event["shipping_address"] = event.pop("address")
    return event
```

### Add a field with a default

```python
def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
    if "priority" not in event:
        event["priority"] = "normal"
    return event
```

### Remove a deprecated field

```python
def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
    event.pop("legacy_tax_code", None)
    return event
```

### Change a field type

```python
def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
    # v1: price in cents (int), v2: price as Money dict
    event["price"] = {"amount": event.pop("price_cents"), "currency": "EUR"}
    return event
```

## Expected outcome

A registered upcaster registry that transforms old-format events to the current schema on read, once. Application code only ever sees the latest event format, a missing upcaster raises `UpcastError` on the first read, and an event read and written back reads the same again.

## Next steps

- [Handle ES Errors](handle-es-errors.md) — UpcastError and other error patterns
- [Create an ES Projection](create-es-projection.md) — projections consume upcasted events

## Cross-references

- **ADR-042**: Event upcaster chain with cycle detection
- **ADR-069**: Upcast events to the class version exactly once (amends ADR-042)
- [Event Versioning concept](../../concepts/es/event-versioning.md)
