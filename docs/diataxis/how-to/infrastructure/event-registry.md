# How to Use the Event Registry

<!-- docs-test: runnable -->

<!-- docs-test: setup
import logging
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.ddd.domain_event import DomainEvent
from pydomain.es import EventStore
from pydomain.infrastructure.bootstrap import bootstrap
from pydomain.infrastructure.event_registry import GenericDomainEvent
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.testing import InMemoryMessageSubscriber

logger = logging.getLogger("docs.event_registry")


class OrderPlaced(DomainEvent):
    order_id: str
    customer_id: str | None = None


class OrderShipped(DomainEvent):
    order_id: str


class OrderCancelled(DomainEvent):
    order_id: str


class ShipmentFailedIntegrationEvent(IntegrationEvent):
    order_id: str
    failure_reason: str


class ExternalShipmentFailed(DomainEvent):
    order_id: UUID
    reason: str


def translate_shipment_failed(
    event: ShipmentFailedIntegrationEvent,
) -> ExternalShipmentFailed:
    return ExternalShipmentFailed(
        order_id=UUID(event.order_id), reason=event.failure_reason
    )


subscriber = InMemoryMessageSubscriber()
message_bus = MessageBus()
-->

> **Prerequisites:** [Event Registry concept](../../concepts/infrastructure/event-registry.md), [Domain Events concept](../../concepts/ddd/domain-events.md)

## Problem

You need to register event types so they can be serialized to the event store and deserialized back, and you need to serialize and deserialize events at runtime.

## Solution

Create an `EventRegistry`, register every domain event type during bootstrap, and use `serialize()` / `deserialize()` at serialization boundaries.

## Steps

### 1. Create the registry

```python
from pydomain.infrastructure.event_registry import EventRegistry


registry = EventRegistry()
```

Optionally pass an `UpcasterRegistry` if you use event versioning:

```python
from pydomain.es.upcasting import UpcasterRegistry


upcasters = UpcasterRegistry()
# ... register upcasters
registry = EventRegistry(upcaster_registry=upcasters)
```

### 2. Register event types

```python
registry.register(OrderPlaced)
registry.register(OrderShipped)
registry.register(OrderCancelled)
```

Register every domain event class that will be persisted. Use the class itself, not a string or instance.

**Registration must happen before any serialization or deserialization.** The typical place is during bootstrap, right after `bootstrap()` returns:

```python
app = await bootstrap(event_registry=EventRegistry())
app.event_registry.register(OrderPlaced)
app.event_registry.register(OrderShipped)
```

`app.event_registry` returns the registry passed to `bootstrap()`, or the empty one it creates when none is passed, so it is never `None` ([ADR-073](../../../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md)).

### 3. Serialize events

```python
event = OrderPlaced(order_id="o1", customer_id="c1")

data = registry.serialize(event)
# → {"type": "OrderPlaced", "data": {"order_id": "o1", "customer_id": "c1"}}
```

The output is a dict suitable for JSON encoding and persistence. The `"type"` key holds the class name; the `"data"` key holds the Pydantic model dump.

### 4. Deserialize events

```python
data = {"type": "OrderPlaced", "data": {"order_id": "o1", "customer_id": "c1"}}

event = registry.deserialize(data)
assert isinstance(event, OrderPlaced)
assert event.order_id == "o1"
```

### 5. Handle unknown event types

When deserializing an unregistered type, the registry returns a `GenericDomainEvent`:

```python
data = {"type": "UnknownEvent", "data": {"foo": "bar"}}

event = registry.deserialize(data)
assert isinstance(event, GenericDomainEvent)
assert event.type == "UnknownEvent"
assert event.data == {"foo": "bar"}
```

Only an unregistered type falls back. A `KeyError` from a registered class's validator, or from an upcaster that overrides `upcast()`, propagates to the caller.

You can inspect the raw data:

<!-- docs-test: skip -->
```python
if isinstance(event, GenericDomainEvent):
    logger.warning(f"Skipping unknown event type: {event.type}")
    return
```

### 6. Look up a class by name

```python
cls = registry.resolve("OrderPlaced")
event = cls(order_id="o1", customer_id="c1")
```

Raises `KeyError` if the type name isn't registered.

### 7. Get the type name for an event instance

```python
name = registry.type_name(OrderPlaced(order_id="o1"))
# → "OrderPlaced"
```

## Using Upcasters for Automatic Deserialization

When you attach an `UpcasterRegistry` to the `EventRegistry`, `deserialize()` automatically applies the upcaster chain before model validation. Application code only ever sees the current event schema — old formats are migrated transparently on read.

### 1. Wire the registry with upcasters during bootstrap

```python
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.bootstrap import bootstrap
from pydomain.infrastructure.event_registry import EventRegistry
from pydomain.es.upcasting import EventUpcaster, UpcasterRegistry


# The current schema: v1 had "amount", v2 renamed it, v3 added "currency"
class OrderPlaced(DomainEvent):
    event_version: int = 3
    order_id: str
    total_amount: int
    currency: str


# Define upcasters
class OrderPlacedV1ToV2(EventUpcaster):
    source_type: ClassVar[str] = "OrderPlaced"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2

    def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
        event["total_amount"] = event.pop("amount")
        return event


class OrderPlacedV2ToV3(EventUpcaster):
    source_type: ClassVar[str] = "OrderPlaced"
    source_version: ClassVar[int] = 2
    target_version: ClassVar[int] = 3

    def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
        if "currency" not in event:
            event["currency"] = "EUR"
        return event


# Build and wire
upcasters = UpcasterRegistry()
upcasters.register(OrderPlacedV1ToV2)
upcasters.register(OrderPlacedV2ToV3)

registry = EventRegistry(upcaster_registry=upcasters)
registry.register(OrderPlaced)  # Register the CURRENT (v3) class

app = await bootstrap(event_registry=registry)
```

### 2. How deserialize applies the chain

Given the above registrations, calling `deserialize()` on a v1 payload:

```python
# Old v1 payload in the event store — has "amount", no "currency"
old_data = {
    "type": "OrderPlaced",
    "version": 1,
    "data": {"order_id": "abc-123", "amount": 9999},
}

event = registry.deserialize(old_data)
# What happens inside deserialize():
# 1. Resolves "OrderPlaced" → OrderPlaced class
# 2. Reads version=1
# 3. UpcasterRegistry.resolve("OrderPlaced", source_version=1)
#    → [OrderPlacedV1ToV2, OrderPlacedV2ToV3]
# 4. Applies chain: v1→v2 (renames amount→total_amount), v2→v3 (adds currency=EUR)
# 5. Checks the chain ended at v3, the default of OrderPlaced.event_version
# 6. Sets event_version=3 and runs model_validate → typed OrderPlaced

assert isinstance(event, OrderPlaced)
assert event.total_amount == 9999
assert event.currency == "EUR"
assert event.event_version == 3
```

The upcaster chain runs **before** Pydantic validation, so the payload is at the current schema by the time `model_validate()` runs. The upcasters do not set `event_version`; `deserialize()` writes the version the chain reached, so `registry.serialize(event)` stores version 3 and reading it back runs no upcaster.

The chain must end at the class's version. If `OrderPlacedV2ToV3` were not registered, the chain would stop at v2 and `deserialize()` would raise `UpcastError: Upcaster chain for 'OrderPlaced' ends at v2, but the class is at v3` rather than validate a v2 payload as v3. Without an upcaster registry nothing is upcast or checked, and `model_validate()` would fail with a validation error because v3 expects `total_amount` but the payload has `amount`.

## Deserializing from the Event Store

This is the primary use case. Events in the event store may span multiple schema versions. The `EventRegistry` with upcaster support ensures they all emerge as current-schema events.

```
┌─────────────────────┐
│ Event Store         │
│ {"type":"OrderPlaced","version":1,"data":{"amount":9999}}
│ {"type":"OrderPlaced","version":2,"data":{"total_amount":9999}}
│ {"type":"OrderPlaced","version":3,"data":{"total_amount":9999,"currency":"EUR"}}
└────────┬────────────┘
         ▼
┌─────────────────────┐
│ EventRegistry       │
│ .deserialize()      │  ← auto-applies upcasters
└────────┬────────────┘
         ▼
    OrderPlaced(        ← always v3
      total_amount=9999,
      currency="EUR"
    )
```

For a concrete example, when the `EventSourcedRepository` reads from the event store, it processes the raw dicts through the registry:

```python
from pydomain.infrastructure.event_registry import EventRegistry
from pydomain.es.upcasting import UpcasterRegistry


class EventStoreReader:
    def __init__(self, event_store: EventStore, registry: EventRegistry) -> None:
        self._store = event_store
        self._registry = registry

    async def read_typed_events(self, aggregate_id: str) -> list[BaseModel]:
        stream = await self._store.read_stream(aggregate_id)

        typed_events: list[BaseModel] = []
        for raw_event in stream.events:  # raw_event is a dict with type/data/version
            event = self._registry.deserialize(raw_event)
            typed_events.append(event)

        return typed_events
```

Each event in the stream is at its own schema version. The registry deserializes every one to the latest version via the upcaster chain, so downstream code only handles current-schema events. A stored version without a complete chain to the class's version raises `UpcastError` on read; a stored version newer than the class (written by newer code) is validated unchanged.

## Integrating with InboundEventGateway

The InboundEventGateway does **not** use the `EventRegistry` for deserialization. It uses topic-based dispatch with explicit `model_validate` because the external contract is defined by the integration event type, not the domain event type:

```python
from pydomain.infrastructure.message_subscriber import (
    InboundEventGateway,
    MessageSubscriber,
)


# message_bus is the bus you pass to bootstrap(message_bus=..., inbound_gateways=[...])
gateway = InboundEventGateway(subscriber, message_bus)

gateway.register_translation(
    topic="shipping.shipment.failed",
    integration_class=ShipmentFailedIntegrationEvent,  # External contract
    translator=translate_shipment_failed,               # Anti-Corruption Layer
)
```

The pipeline:
1. Raw JSON arrives from the broker → dict
2. `ShipmentFailedIntegrationEvent.model_validate(dict)` → typed integration event
3. `translate_shipment_failed(integration_event)` → `DomainEvent`
4. `message_bus.dispatch(domain_event)` → internal handlers

If the external system evolves its schema, handle the change in the translator:

```python
class ShipmentFailedIntegrationEventV2(IntegrationEvent):
    order_id: str
    failure: dict[str, str]  # Now nested: {"code": "...", "detail": "..."}


def translate_shipment_failed_v2(
    event: ShipmentFailedIntegrationEventV2,
) -> ExternalShipmentFailed:
    return ExternalShipmentFailed(
        order_id=UUID(event.order_id),
        reason=f"{event.failure['code']}: {event.failure['detail']}",
    )


# Register the new version — old topic handlers are replaced
gateway.register_translation(
    topic="shipping.shipment.failed",
    integration_class=ShipmentFailedIntegrationEventV2,
    translator=translate_shipment_failed_v2,
)
```

The `EventRegistry` is used for domain events (event store path); the `InboundEventGateway` handles integration events with its own hydration (`model_validate` directly). These are separate concerns because integration events carry the external system's schema contract, not the domain's.

### Comparing the Two Deserialization Paths

| Aspect | Event Store Path | InboundEventGateway Path |
|--------|-----------------|--------------------------|
| Input | `{"type": "...", "data": {...}}` dict with discriminator | Raw JSON dict, topic-implied type |
| Deserialization | `EventRegistry.deserialize()` | `IntegrationEvent.model_validate()` |
| Versioning | `UpcasterRegistry` chain auto-applied | Translator function handles changes |
| Fallback for unknown | `GenericDomainEvent` | Logged and discarded |
| Output | `DomainEvent` subclass | `DomainEvent` (via ACL translator) |

## Duplicate Registration

Registering the same class twice raises `ValueError`:

<!-- docs-test: skip -->
```python
registry.register(OrderPlaced)
registry.register(OrderPlaced)
# → ValueError: Event 'OrderPlaced' is already registered
```

Use `resolve()` to check if a type is already registered:

```python
try:
    registry.resolve("OrderPlaced")
    already_registered = True
except KeyError:
    already_registered = False
```

## Expected Outcome

After registration, the `EventRegistry` can serialize any registered event to `{"type": ..., "data": ...}` and deserialize it back to the correct Pydantic model — with automatic upcaster chain application if an `UpcasterRegistry` is attached. Unknown types fall back to `GenericDomainEvent` rather than crashing.

When reading from the event store, events at any schema version emerge as current-schema events. When ingesting from external brokers via `InboundEventGateway`, integration events are hydrated directly and translated through the Anti-Corruption Layer.

## See Also

- [Event Registry concept](../../concepts/infrastructure/event-registry.md)
- [Bootstrap the Application](bootstrap-application.md) — where registration happens
- [Event Versioning](../../concepts/es/event-versioning.md)
- [Implement an Upcaster](../../how-to/event-sourcing/implement-upcaster.md)
- [ADR-069](../../../adr/ADR-069-upcast-to-class-version-once.md) — upcast events to the class version exactly once
- [InboundEventGateway concept](../../concepts/infrastructure/inbound-event-gateway.md)
- [Configure InboundEventGateway](configure-inbound-event-gateway.md)
