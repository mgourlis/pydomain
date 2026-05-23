# ADR-030: SagaRegistry Auto-Binding via `listens_to`

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Each saga class handles specific domain events. The saga manager needs to know which event types route to which saga classes. Manual registration (calling `registry.register(EventType, SagaClass)` for every combination) is error-prone and couples the registration code to the saga's internal event handling.

## Decision

Each `Saga` class declares `listens_to: ClassVar[list[type[DomainEvent]]]`. The `SagaRegistry` uses this to auto-bind event types to saga instances:

```python
class Saga[S: SagaState]:
    listens_to: ClassVar[list[type[DomainEvent]]] = []

    @classmethod
    def listened_events(cls) -> list[type[DomainEvent]]:
        if cls.listens_to:
            return list(cls.listens_to)
        return []
```

Registration:

```python
class SagaRegistry:
    def register_saga(self, saga_class, *, strict=False):
        events = saga_class.listened_events()
        for event_type in events:
            self.register(event_type, saga_class)
        # Also register by name for recovery lookups
        self.register_type(saga_class)
```

Usage:

```python
class OrderSaga(Saga[OrderSagaState]):
    listens_to = [OrderCreated, ItemsReserved, PaymentReceived]

# Single call registers for all three event types
saga_registry.register_saga(OrderSaga)
```

The registry also supports manual per-event registration via `register(event_type, saga_class)` for advanced scenarios.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Manual per-event registration | Verbose; easy to forget an event; registration code must mirror saga internals |
| Decorator-based registration (`@handles(OrderCreated)`) | Requires import-time side effects; harder to test; decorator ordering issues |
| Reflection over `on()` calls | Fragile — depends on `__init__` execution order; cannot inspect without instantiation |

## Consequences

### Positive

- Single `register_saga()` call wires all event bindings.
- `listens_to` is a class-level declaration — visible, auditable, IDE-navigable.
- Registry maintains both event-type → saga and name → saga mappings (for recovery).
- `strict=True` raises on empty `listens_to` instead of silently ignoring.

### Negative

- Forgetting to set `listens_to` means the saga receives no events (mitigated by `strict=True` and warning log).

### Neutral

- Multiple sagas can listen to the same event type — the registry stores a list per event type.

## References

- `src/pydomain/cqrs/saga/saga.py` — `Saga.listens_to` ClassVar and `listened_events()` method
- `src/pydomain/cqrs/saga/registry.py` — `SagaRegistry.register_saga()` auto-binding
