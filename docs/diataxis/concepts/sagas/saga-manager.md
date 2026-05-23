# Saga Manager

> **Adoption Level:** 5 — Sagas & Process Managers
> **Module:** `pydomain.cqrs.saga.manager`
> **Prerequisites:** [Saga](saga.md), [Saga Registry](saga-registry.md), [Saga Repository](saga-repository.md), [Command Bus](../cqrs/command-bus.md)

## What is the SagaManager?

The `SagaManager` orchestrates the full saga lifecycle for each incoming event:

```
Event → find saga classes → load/create state → instantiate saga → handle(event) → save state → dispatch commands
```

It bridges the event-driven world (domain events arriving from a message bus) with the command-driven world (dispatching commands through the command bus).

## Constructor

```python
from pydomain.cqrs.saga import SagaManager, SagaRegistry

manager = SagaManager(
    repository=saga_repository,
    registry=saga_registry,
    command_bus=command_bus,
)
```

## Core API

### `handle(event)` — event-driven entry point

```python
await manager.handle(event)
```

Called for each incoming domain event. The manager:
1. Looks up all saga classes registered for this event type
2. Extracts `correlation_id` from the event
3. For each saga: loads or creates state, processes the event, saves, dispatches commands
4. Returns immediately if no sagas are registered or `correlation_id` is missing

### `start_saga(saga_class, initial_event)` — orchestration entry point

```python
saga_id = await manager.start_saga(
    OrderFulfillmentSaga,
    initial_event=order_created_event,
    correlation_id=order_correlation_id,
)
```

Explicitly starts (or continues) a saga. Generates a `correlation_id` if none is provided.

### `bind_to(event_dispatcher)` — auto-register with event bus

```python
manager.bind_to(message_bus)
```

Reads all event types from the registry and registers `manager.handle` as the handler for each. Equivalent to manually calling `event_dispatcher.register_event(event_type, manager.handle)` for every registered event type.

### `recover_pending_sagas(limit)` — crash recovery

```python
await manager.recover_pending_sagas(limit=50)
```

Finds sagas with undispatched `pending_commands` and re-dispatches them. Handles three scenarios:
- **Stalled dispatch:** Commands queued but not marked dispatched → re-dispatch
- **Compensating sagas:** Stalled during compensation → resume compensation
- **Retry exhaustion:** `retry_count >= max_retries` → force-fail with compensation

### `process_timeouts(limit)` — timeout handling

```python
await manager.process_timeouts(limit=10)
```

Finds suspended sagas whose `timeout_at` has passed, calls `saga.on_timeout()`, and dispatches any resulting commands. If the timeout handler doesn't resolve the suspension, the saga is force-failed.

## Processing pipeline

The private `_process_saga` method runs the full pipeline for a single saga:

1. **Load or create state** — `find_by_correlation_id()` or create new `SagaState`
2. **Skip terminal** — if `is_terminal`, return immediately
3. **Check retries** — if `retry_count >= max_retries`, force-fail
4. **Resume if suspended** — call `saga.resume()` if status is SUSPENDED and `should_resume()` passes
5. **Handle event** — `await saga.handle(event)`
6. **Collect commands** — `saga.collect_commands()`
7. **Dispatch** — compensation path or forward path, with tracing

## Tracing propagation

The manager propagates `correlation_id` and `causation_id` onto every dispatched command via `model_copy`:

```python
cmd = cmd.model_copy(update={
    "correlation_id": state.correlation_id,
    "causation_id": causation_id,
})
```

This ensures the full causal chain is traceable across services.

## Crash recovery design

Each command is persisted to `state.pending_commands` **before** dispatch with `dispatched: False`. After successful dispatch, it's marked `dispatched: True`. On recovery, any command still marked `False` is re-dispatched.

This design handles crashes between dispatch calls without double-processing — the command handler's own idempotency (via [Idempotency & Locking](../cqrs/idempotency-and-locking.md)) guards against the edge case where the command executed but the dispatch confirmation was lost.

## Next steps

- [Saga Lifecycle](saga-lifecycle.md) — the state machine the manager drives
- [How to Configure a Saga Manager](../../how-to/sagas/configure-saga-manager.md) — wire up in your application
- [How to Handle Saga Errors](../../how-to/sagas/saga-error-handling.md) — failure modes and recovery
