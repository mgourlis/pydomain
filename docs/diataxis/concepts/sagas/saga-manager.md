# Saga Manager

> **Adoption Level:** 5 — Sagas & Process Managers
> **Module:** `pydomain.cqrs.saga.manager`
> **Prerequisites:** [Saga](saga.md), [Saga Registry](saga-registry.md), [Saga Repository](saga-repository.md), [Command Bus](../cqrs/command-bus.md)

## What is the SagaManager?

The `SagaManager` orchestrates the full saga lifecycle for each incoming event:

```
Event → find saga classes → load/create state → instantiate saga → handle(event) → save state → dispatch commands
```

It bridges the event-driven world (domain events arriving from a message bus) with the command-driven world (dispatching commands through a dispatcher).

## Constructor

```python
from pydomain.cqrs.saga import SagaManager, SagaRegistry
from pydomain.infrastructure.message_bus import MessageBus

message_bus = MessageBus()

manager = SagaManager(
    repository=saga_repository,
    registry=saga_registry,
    command_bus=message_bus,
)
manager.bind_to(message_bus)
```

The `command_bus` argument is a `CommandDispatcher`: anything whose `async dispatch(command)` runs the command **and publishes the domain events it raised**, so that a saga sees the events its own commands cause. The `MessageBus` does. A `CommandBus` returns those events instead of publishing them, so a saga wired to it would never advance; `SagaManager` raises `TypeError` for a `CommandBus` or a subclass of it and says to pass the `MessageBus` ([ADR-077](../../../adr/ADR-077-saga-failure-and-recovery-keep-every-command.md)). A test can pass any object with a matching `dispatch` method.

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

Reads all event types from the registry and registers `manager.handle` as the handler for each. Equivalent to manually calling `event_dispatcher.register_event(event_type, manager.handle)` for every registered event type. The argument is an `EventHandlerRegistrar`: any object with that `register_event` method, such as the `MessageBus`.

### `recover_pending_sagas(limit)` — crash recovery

```python
not_recovered = await manager.recover_pending_sagas(limit=50)
```

Finds the stalled sagas, those with `pending_commands` or in `COMPENSATING`, and recovers each one. Handles these scenarios:
- **Stalled dispatch:** commands queued but not marked dispatched → re-dispatch. Each attempt adds one to `retry_count`; success resets it to 0.
- **Unhydratable command:** recovery sends pending commands in order and stops at the first one whose class can no longer be imported (a renamed module or class): it and the commands after it are not sent and stay in `pending_commands`, which keeps their order. The attempt counts as failed. A saga that was not suspended is suspended with the reason `Could not hydrate <CommandType>`, and recovery marks that suspension as its own with `"suspended_by_recovery": True` on the command's `pending_commands` entry, next to `"dispatched"`. A saga already suspended keeps its own reason and `timeout_at`; recovery never resumes or rewrites a suspension it did not mark, whatever its reason says. Once the import path works again, the next recovery sends the waiting commands in order, and a suspension recovery marked ends: the saga is `RUNNING` again (also a hand-built state that was still `PENDING`). An event that resumes the saga first clears the mark.
- **Compensating sagas:** stalled during compensation → resume compensation. One recovery ends the saga `COMPENSATED`, or `FAILED` when any compensation failed or could not be hydrated; such a saga is in the returned list.
- **Retry exhaustion:** `retry_count > max_retries` → force-fail with compensation. `max_retries` is the number of retries after the first attempt (default 3); `0` allows none, and `None` allows any number.

One saga's failure does not stop the others: it is logged at ERROR with the saga id and the exception's type, and recovery moves on. The method returns the ids of the sagas it did not recover in this run (a failed dispatch, an unhydratable command, an unregistered saga type, a compensation that ended `FAILED`), or an empty list.

### `process_timeouts(limit)` — timeout handling

```python
await manager.process_timeouts(limit=10)
```

Finds suspended sagas whose `timeout_at` has passed, calls `saga.on_timeout()`, and dispatches any resulting commands. If the timeout handler doesn't resolve the suspension, the saga is force-failed.

## Processing pipeline

The private `_process_saga` method runs the full pipeline for a single saga:

1. **Load or create state** — `find_by_correlation_id()` or create new `SagaState`
2. **Skip terminal** — if `is_terminal`, return immediately
3. **Check retries** — if `retry_count > max_retries` (and `max_retries` is not `None`), force-fail
4. **Resume if suspended** — call `saga.resume()` if status is SUSPENDED and `should_resume()` passes
5. **Handle event** — `await saga.handle(event)`
6. **Collect commands** — `saga.collect_commands()`
7. **Dispatch** — compensation path or forward path, with tracing. On the compensation path the forward commands the step queued before it failed (a `fail=True` step's `send`, or `dispatch()` then `await fail()`) go first, caused by the step's event, then the compensations in LIFO order

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
