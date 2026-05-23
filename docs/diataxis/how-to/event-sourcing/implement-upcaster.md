# How to Implement an Upcaster

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
```

The `upcast()` method wraps `_transform` — if it raises, the exception becomes an `UpcastError` with diagnostic context.

## 3. Register upcasters

Add them to the `UpcasterRegistry`:

```python
from pydomain.es.upcasting import UpcasterRegistry


registry = UpcasterRegistry()
registry.register(OrderPlacedV1ToV2)
registry.register(OrderPlacedV2ToV3)
```

## 4. Resolve and apply the chain

Given an event's type and source version, resolve the upcaster chain:

```python
chain = registry.resolve("OrderPlaced", source_version=1)
# Returns [OrderPlacedV1ToV2, OrderPlacedV2ToV3]

# Apply the chain:
event_payload = {"total": 1000, "order_id": "..."}
for upcaster_cls in chain:
    upcaster = upcaster_cls()
    event_payload = upcaster.upcast(event_payload)

# event_payload is now at v3: {"total_amount": 1000, "currency": "EUR", "order_id": "..."}
```

If the registry resolves an empty chain, the event is already at the latest known format — no upcasting needed.

## 5. Common transformations

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

A registered upcaster registry that transforms old-format events to the current schema on read. Application code only ever sees the latest event format.

## Next steps

- [Handle ES Errors](handle-es-errors.md) — UpcastError and other error patterns
- [Create an ES Projection](create-es-projection.md) — projections consume upcasted events

## Cross-references

- **ADR-042**: Event upcaster chain with cycle detection
