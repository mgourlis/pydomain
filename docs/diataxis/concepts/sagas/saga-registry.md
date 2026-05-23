# Saga Registry

> **Adoption Level:** 5 — Sagas & Process Managers
> **Module:** `pydomain.cqrs.saga.registry`
> **Prerequisites:** [Saga](saga.md)

## What is the SagaRegistry?

The `SagaRegistry` maps event types to saga classes. When an event arrives, the `SagaManager` queries the registry to find which sagas should handle it.

```python
from pydomain.cqrs.saga import SagaRegistry

registry = SagaRegistry()
registry.register_saga(OrderFulfillmentSaga)
```

## Registration methods

### `register_saga(saga_class)` — bulk registration (preferred)

Reads the class-level `listens_to` declaration and registers the saga for each event type automatically:

```python
class OrderFulfillmentSaga(Saga[SagaState]):
    listens_to = [OrderCreated, PaymentConfirmed, ItemsShipped]

registry.register_saga(OrderFulfillmentSaga)
# Registers: OrderCreated → OrderFulfillmentSaga
#            PaymentConfirmed → OrderFulfillmentSaga
#            ItemsShipped → OrderFulfillmentSaga
```

Pass `strict=True` to raise `SagaConfigurationError` if `listens_to` is empty (instead of logging a warning).

### `register(event_type, saga_class)` — per-event registration

```python
registry.register(OrderCreated, OrderFulfillmentSaga)
registry.register(PaymentConfirmed, OrderFulfillmentSaga)
```

Use when you need fine-grained control or when the same saga class has different configurations for different events.

### `register_type(saga_class)` — name-only registration

```python
registry.register_type(OrderFulfillmentSaga)
```

Registers the saga by `__name__` only (for recovery lookups), without binding any events. Useful for sagas started exclusively via `start_saga()` rather than event routing.

## Query methods

### `get_sagas_for_event(event_type)`

```python
saga_classes = registry.get_sagas_for_event(OrderCreated)
# → [OrderFulfillmentSaga, AuditLogSaga]
```

Multiple sagas can react to the same event type — this returns all of them.

### `get_saga_type(name)`

```python
saga_class = registry.get_saga_type("OrderFulfillmentSaga")
```

Used by the `SagaManager` for recovery and timeout handling, where only the saga type name is persisted in state.

### `registered_event_types`

```python
for event_type in registry.registered_event_types:
    print(event_type.__name__)
```

Returns all event types that have at least one saga registered. Used by `bind_to()` for auto-registration with the event dispatcher.

## Multiple sagas per event

A single event type can trigger multiple sagas:

```python
registry.register(OrderCreated, OrderFulfillmentSaga)
registry.register(OrderCreated, CustomerNotificationSaga)
registry.register(OrderCreated, InventoryTrackingSaga)
```

When `OrderCreated` arrives, all three sagas process it independently. Each has its own `correlation_id`-scoped state.

## Lifecycle

- **Registration** happens at application startup (bootstrap phase)
- **Clearing** (`registry.clear()`) removes all registrations — mainly useful in tests
- **Thread safety:** The registry is not thread-safe by default; register all sagas before the application starts handling events

## Next steps

- [Saga Manager](saga-manager.md) — how the manager uses the registry
- [How to Configure a Saga Manager](../../how-to/sagas/configure-saga-manager.md) — wire registry + manager + bus
