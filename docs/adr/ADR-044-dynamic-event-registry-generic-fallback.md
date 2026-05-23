# ADR-044: Dynamic Event Registry with `GenericDomainEvent` Fallback

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Event-sourced systems need to serialize and deserialize events for persistence and messaging. The challenge: the registry must map type names (strings) to Pydantic model classes at runtime, because:

1. Events are stored as `{"type": "OrderPlaced", "data": {...}}` — the type name is a string.
2. New event types are added over time — the registry must be extensible.
3. Legacy events may have been removed from the codebase — the registry must handle unknown types gracefully.

Static discriminated unions (Pydantic `Discriminator`) don't work because they require compile-time enumeration of all event types.

## Decision

`EventRegistry` provides dynamic type-to-class mapping with a weak-schema fallback:

```python
class EventRegistry:
    def __init__(self, upcaster_registry=None):
        self._registry: dict[str, type[BaseModel]] = {}

    def register(self, event_class: type[BaseModel]) -> None:
        # Raises ValueError if already registered
        self._registry[event_class.__name__] = event_class

    def serialize(self, event: BaseModel) -> dict[str, Any]:
        # {"type": "OrderPlaced", "data": {...}, "version": 1}
        result = {"type": type(event).__name__, "data": event.model_dump()}
        if hasattr(event, "event_version"):
            result["version"] = event.event_version
        return result

    def deserialize(self, data: dict[str, Any]) -> BaseModel | GenericDomainEvent:
        type_name = data.get("type")
        try:
            cls = self.resolve(type_name)
            # Apply upcasters if available
            return cls.model_validate(payload)
        except KeyError:
            # Unknown type → weak-schema fallback
            return GenericDomainEvent(type=type_name, data=payload, version=version)
```

**`GenericDomainEvent`** is the fallback for unregistered types:

```python
class GenericDomainEvent(BaseModel):
    type: str
    data: dict[str, Any]
    version: int = 1
```

This allows the system to handle legacy events that have been removed from the codebase without raising errors — the raw data is preserved.

**Upcaster integration**: The registry chains upcasters during deserialization:

```python
if self._upcaster_registry is not None:
    upcasters = self._upcaster_registry.resolve(type_name, version)
    for upcaster_cls in upcasters:
        payload = upcaster_cls().upcast(payload)
```

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Pydantic discriminated union | Requires compile-time type enumeration; not dynamic |
| Raise on unknown type | Legacy events crash the system; no graceful degradation |
| Auto-discovery via module scanning | Fragile; depends on import order; explicit registration is clearer |

## Consequences

### Positive

- Dynamic registration — add event types at startup without code generation.
- `GenericDomainEvent` handles legacy/unknown events gracefully — no crash.
- Upcaster integration ensures old-format events are transformed before deserialization.
- `serialize()` includes `version` for schema evolution support.

### Negative

- `GenericDomainEvent` carries untyped data — callers must handle raw dicts.
- Registration is manual — must call `register()` for every event type.

### Neutral

- The registry is injected into the bootstrap process — tests use a fresh instance.

## References

- `src/pydomain/infrastructure/event_registry.py` — `EventRegistry`, `GenericDomainEvent`
- `src/pydomain/es/upcasting.py` — `UpcasterRegistry` integration
