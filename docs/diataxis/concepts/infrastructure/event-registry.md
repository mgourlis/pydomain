# Event Registry

> **Adoption Level:** 3 — CQRS with Events
> **Module:** `pydomain.infrastructure.event_registry`

## What is the Event Registry?

The **Event Registry** maps event type names to Pydantic model classes. It enables serialization (event → dict) and deserialization (dict → event) without hard-coding type-to-class mappings.

When you persist an event to the event store or publish it over a message broker, the registry stamps it with a `"type"` discriminator. When you read it back, the registry uses that discriminator to reconstruct the correct Pydantic model.

## Why It Exists

Events cross serialization boundaries: event store writes, message broker publishes, snapshot serialization. Without a registry, you'd need `if/elif` chains or manual mappings at every boundary:

```python
# Without a registry — fragile, repetitive
def deserialize(data: dict) -> DomainEvent:
    type_name = data["type"]
    if type_name == "OrderPlaced":
        return OrderPlaced(**data["data"])
    elif type_name == "OrderShipped":
        return OrderShipped(**data["data"])
    # ... every event type
```

The registry turns this into a single lookup:

```python
event = registry.deserialize(data)  # Works for any registered type
```

## The `EventRegistry` Class

```python
from pydomain.infrastructure.event_registry import EventRegistry


class EventRegistry:
    def __init__(self, upcaster_registry: UpcasterRegistry | None = None) -> None: ...

    def register(self, event_class: type[BaseModel]) -> None: ...
    def resolve(self, type_name: str) -> type[BaseModel]: ...
    def type_name(self, event: BaseModel) -> str: ...
    def serialize(self, event: BaseModel) -> dict[str, Any]: ...
    def deserialize(self, data: dict[str, Any]) -> BaseModel | GenericDomainEvent: ...
```

### `register(event_class)`

Register a Pydantic model class by its `__name__`. Raises `ValueError` if the class is already registered.

```python
registry = EventRegistry()
registry.register(OrderPlaced)
registry.register(OrderShipped)
registry.register(OrderCancelled)
```

Only one class per name is allowed — two classes can't share the same `__name__`.

### `resolve(type_name)`

Return the registered class for a discriminator string. Raises `KeyError` if not found.

```python
cls = registry.resolve("OrderPlaced")
event = cls(**data)
```

### `type_name(event)`

Return the `__name__` of an event instance — the key used during registration.

```python
name = registry.type_name(OrderPlaced(order_id="o1"))
# → "OrderPlaced"
```

### `serialize(event)`

Convert an event instance to a `{"type": "...", "data": {...}}` dict:

```python
data = registry.serialize(OrderPlaced(order_id="o1", customer_id="c1"))
# → {"type": "OrderPlaced", "data": {"order_id": "o1", "customer_id": "c1"}}
```

For registered types, the dict is `{"type": <name>, "data": <model_dump>}`.

### `deserialize(data)`

Reconstruct an event from a `{"type": "...", "data": {...}}` dict:

```python
event = registry.deserialize({"type": "OrderPlaced", "data": {"order_id": "o1"}})
assert isinstance(event, OrderPlaced)
```

The `"type"` key is mandatory — raises `ValueError` if missing.

## The `GenericDomainEvent` Fallback

When `deserialize()` encounters an unregistered type name, it returns a `GenericDomainEvent` instead of raising an error:

```python
from pydomain.infrastructure.event_registry import GenericDomainEvent


class GenericDomainEvent(BaseModel):
    type: str
    data: dict[str, Any]
    version: int = 1
```

This weak-schema fallback means your application won't crash when reading events it doesn't recognize — useful for forward compatibility with events written by newer versions of the application.

You can still access the raw data:

```python
event = registry.deserialize(data)
if isinstance(event, GenericDomainEvent):
    logger.warning(f"Unknown event type: {event.type}")
    raw = event.data  # dict[str, Any]
```

## Serialization Format

The wire format is a three-key dict:

```json
{
  "type": "OrderPlaced",
  "data": {
    "order_id": "abc-123",
    "customer_id": "c-456",
    "total_amount": "99.99"
  }
}
```

With upcasters, a `"version"` key is added to support schema evolution:

```json
{
  "type": "OrderPlaced",
  "version": 2,
  "data": {
    "order_id": "abc-123",
    "total": "99.99"
  }
}
```

## Automatic Deserialization with Upcaster Registry

The `__init__` accepts an optional `UpcasterRegistry`:

```python
from pydomain.es.upcasting import UpcasterRegistry
from pydomain.infrastructure.event_registry import EventRegistry


upcasters = UpcasterRegistry()
# ... register upcasters ...
registry = EventRegistry(upcaster_registry=upcasters)
```

When an `UpcasterRegistry` is attached, `deserialize()` automatically resolves and applies the upcaster chain before Pydantic validation:

```
deserialize(data)
    │
    ├── 1. Read "type" discriminator → resolve class
    ├── 2. Read "version" key (default 1)
    ├── 3. If upcaster_registry is attached:
    │       upcasters = upcaster_registry.resolve(type_name, version)
    │       for upcaster in upcasters:
    │           payload = upcaster.upcast(payload)
    ├── 4. cls.model_validate(payload) → typed event
    └── Return event
```

This means the application code never sees old event formats — by the time `deserialize()` returns, the event is at the latest schema version.

### From the Event Store

When reading from the event store, events may be at various schema versions. The repository serializes events through the `EventRegistry` on write, and deserializes on read with automatic upcasting:

```
┌──────────────────┐
│ Event Store      │  Raw dicts at v1, v2, v3
│ (persisted JSON) │
└────────┬─────────┘
         ▼
┌──────────────────┐
│ EventRegistry    │  deserialize(payload)
│ + UpcasterReg.   │  ├── resolve upcaster chain
│                  │  ├── v1→v2→v3 (auto-applied)
│                  │  └── model_validate(v3)
└────────┬─────────┘
         ▼
┌──────────────────┐
│ Typed Event      │  Application code only sees
│ (latest schema)  │  the current event format
└──────────────────┘
```

```python
# Old V1 event persisted in the store:
persisted = {"type": "OrderPlaced", "version": 1, "data": {"amount": "99.99"}}

# Deserialize — upcaster chain auto-applied, returns V2 OrderPlaced
event = registry.deserialize(persisted)
assert isinstance(event, OrderPlaced)
# event.total_amount == 99.99  (migrated from v1's "amount")
```

### From the InboundEventGateway

The InboundEventGateway uses a different mechanism — it hydrates raw JSON payloads into `IntegrationEvent` subclasses via `model_validate`, then translates them to `DomainEvent` instances via an Anti-Corruption Layer translator. The `EventRegistry` is not used in this path because integration events carry their own schema contract defined by the external system.

```
External Broker (Kafka/RabbitMQ)
        │
        ▼
┌──────────────────────────────────────┐
│ InboundEventGateway                  │
│ 1. integration_class.model_validate │  ← typed IntegrationEvent
│ 2. translator(integration_event)    │  ← Anti-Corruption Layer
│ 3. message_bus.dispatch(domain_event)│
└──────────────────────────────────────┘
```

If the integration event's schema changes over time, versioning is handled at the translator level — the translator function maps the external contract to domain types, applying any migration logic needed for external schema changes.

## Registration at Bootstrap

All event types must be registered during application startup, before any dispatch occurs:

```python
from pydomain.es.upcasting import UpcasterRegistry


upcasters = UpcasterRegistry()
# ... register upcasters ...
app = await bootstrap(event_registry=EventRegistry(upcaster_registry=upcasters))

app.event_registry.register(OrderPlaced)
app.event_registry.register(OrderShipped)
app.event_registry.register(OrderCancelled)
```

If an event type reaches the event store without being registered, it will still serialize (using its `__class__.__name__`) but deserialization will return a `GenericDomainEvent`.

## Design Decision

The registry uses `__name__` as the discriminator rather than a fully-qualified path. This is simpler and shorter on the wire, but means two event classes with the same name (even in different modules) cannot coexist. If you need that, qualify your class names (e.g., `OrderPlacedV1`, `OrderPlacedV2`).

The upcaster registry is injected via the constructor rather than registered-per-event, keeping the upcaster concern separate from event type registration. This means the same upcaster registry can be shared across multiple `EventRegistry` instances if needed.

> **📌 ADR-044**: Dynamic Event Registry with GenericDomainEvent Fallback

## Relationship to Other Concepts

- **Event Store**: persists serialized events; the EventRegistry deserializes them on read with automatic upcasting
- **Event Versioning / UpcasterRegistry**: the upcaster chain that runs during `deserialize()`
- **InboundEventGateway**: hydrates external messages via `model_validate` directly — does not use the EventRegistry; versioning of external payloads is handled at the translator level
- **Message Broker**: uses `EventRegistry.serialize()` to produce the wire format for outbound events

The registry uses `__name__` as the discriminator rather than a fully-qualified path. This is simpler and shorter on the wire, but means two event classes with the same name (even in different modules) cannot coexist. If you need that, qualify your class names (e.g., `OrderPlacedV1`, `OrderPlacedV2`).

Related ADR: [ADR-044 — Dynamic Event Registry with GenericDomainEvent Fallback](../../../adr/ADR-044-dynamic-event-registry-generic-fallback.md)

## Next Steps

- **[Use the Event Registry with Upcasters →](../../how-to/infrastructure/event-registry.md)** — register and serialize events with automatic upcasting
- **[Application Bootstrap →](bootstrap.md)** — where the registry is wired
- **[Event Store →](../es/event-store.md)** — where serialized events are persisted and read back
- **[Event Versioning →](../es/event-versioning.md)** — upcasters and schema evolution
- **[InboundEventGateway →](inbound-event-gateway.md)** — external event ingestion (uses its own hydration, not EventRegistry)
- **[Message Broker →](message-broker.md)** — publishing events across boundaries
