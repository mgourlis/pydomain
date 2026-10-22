# Command Bus

> **Adoption Level:** 2 — CQRS
> **Module:** `pydomain.cqrs.command_bus`

## What is the Command Bus?

The **Command Bus** routes commands to their single registered handler and returns a typed result. It manages the full transactional lifecycle: Unit of Work creation, pipeline execution, commit/rollback, and event collection.

## Architecture

```
CommandBus.dispatch(command)
  │
  ├── 1. Look up handler + UoW factory for command type
  ├── 2. Create UoW via factory
  ├── 3. Build MessageContext (tracing IDs, metadata)
  ├── 4. Enter the UoW (async with uow)
  ├── 5. Run pipeline: behaviors → terminal step
  │        terminal step: handler → uow.commit() (stamps events) → ctx.new_events
  │        then each behavior's code after next(), innermost first
  ├── 6. On failure: leave the UoW context (it rolls back once), raise CommandExecutionError
  └── 7. Return (result, events): the terminal step's copy of the committed events
```

## Registration

Handlers are registered **per command type** along with a UoW factory and optional pipeline behaviors:

```python
from pydomain.cqrs.command_bus import CommandBus

bus = CommandBus()

bus.register(
    command_type=PlaceOrder,
    handler=PlaceOrderHandler(pricing_service),
    uow_factory=lambda: OrderUoW(session_factory()),
    behaviors=[LoggingBehavior(), ValidationBehavior()],
)
```

Each command type can have exactly one handler. Registering a second handler for the same type raises `HandlerAlreadyRegisteredError`.

## Dispatch

`dispatch()` returns a `(result, events)` tuple:

```python
result, events = await bus.dispatch(PlaceOrder(
    order_id=order_id,
    customer_id=customer_id,
    items=[OrderLine(...)],
))

# result: PlaceOrderResult — typed
# events: list[DomainEvent] — stamped with tracing IDs
```

If no handler is registered for the command type, `NoHandlerRegisteredError` is raised.

## Transactional Lifecycle

Every command runs inside a Unit of Work context:

1. **UoW created** via the registered factory — fresh per dispatch, and entered before the first behavior
2. **Pipeline executes** — behaviors wrap the terminal step in onion order
3. **Terminal step:** the handler runs, then `uow.commit()` persists its changes and collects the events, then `ctx.new_events` is set. The commit runs inside every behavior, so a behavior's code after `next()` runs after it: locks are held through the commit, and the idempotency record follows it
4. **Short-circuit:** a behavior that returns without calling `next()` (a cached duplicate) skips the terminal step, so nothing is committed; the scope rolls back on exit and no events are returned
5. **On failure:** the bus leaves the `async with uow` block, and the UoW's `__aexit__` rolls back, once. The rollback discards the events the handler staged, also in a repository shared with other units of work. The bus then raises `CommandExecutionError` with the original exception as its `__cause__`
6. **On cancellation:** a cancellation before the storage commit rolls back like a failure, and propagates unwrapped. One that arrives natively (`task.cancel()`, a timeout) while `_commit()` runs waits for it: the scope is committed, nothing rolls back, `IdempotencyBehavior` records the result, and the cancellation propagates. Under an anyio cancel scope the cancellation arrives at the next checkpoint instead, so a dispatch with none left returns normally, with its events. The committed events are not returned, so `MessageBus` does not hand them to in-process event handlers; the outbox delivers them ([Unit of Work: Cancellation](unit-of-work.md#cancellation))

The handler never calls `commit()` or `rollback()` — the bus owns the transaction boundary ([ADR-020](../../../adr/ADR-020-commandbus-owns-uow-lifecycle.md)). A handler that commits anyway makes the terminal step's commit raise `UnitOfWorkAlreadyCommittedError`, so the dispatch fails with `CommandExecutionError` (cause: `UnitOfWorkAlreadyCommittedError`) instead of committing twice. The handler's own commit **is** persisted, and the scope is not rolled back: a committed scope has nothing to roll back. Its events are never dispatched in-process, because the bus raises before it returns them; they reach consumers only through the outbox, if a writer is configured. With `IdempotencyBehavior`, the committed command is recorded, so a retry gets its result instead of failing again. Anything the handler saved after its commit is drained when the scope exits.

If the rollback itself raises, the caller still gets `CommandExecutionError`: its `__cause__` is the original failure, its `__context__` the rollback's exception, and a note, shown in tracebacks, names the rollback exception's type (`clean-up also failed: ConnectionError`) but never its message ([ADR-068](../../../adr/ADR-068-rollback-drains-event-buffers.md)). The same note appears when a committed scope's drain raises after a handler's own commit. A failure while entering the UoW (`__aenter__`) is not wrapped, and neither is a cancellation. A cancelled dispatch whose rollback raises still raises the cancellation, with the same type-only note; the rollback's exception is its `__context__` ([ADR-078](../../../adr/ADR-078-cleanup-errors-stored-versions-and-per-class-configure.md)). If the drain on exit raises after a successful commit, that exception propagates unwrapped and no events are returned, though the commit persisted.

Give each dispatch its own UoW: the factory must build a new one on every call. A factory that returns one shared instance (`lambda: uow`) makes nested dispatches, such as an event handler that dispatches a command, share one scope.

## Tracing ID Propagation

The bus resolves tracing IDs from the command:

```python
correlation_id = command.correlation_id or command.command_id
causation_id = command.causation_id or command.command_id
```

When a [Saga](../sagas/saga.md) dispatches a command with explicit tracing IDs, those are used. For direct dispatches, `command_id` serves as both. The resolved IDs are stamped onto the UoW so events collected during `commit()` carry the correct chain.

## Pipeline Behaviors

The bus wraps each handler in a [Pipeline](pipeline-behaviors.md) that executes behaviors in onion order around the terminal step:

```
Outermost behavior
  └── Middle behavior
        └── Innermost behavior
              └── Terminal step: your handler, then uow.commit()
```

Logs and behaviors still see your handler as `ctx.handler`. [What `next()` means in a command pipeline](pipeline-behaviors.md#what-next-means-in-a-command-pipeline) lists what a behavior can rely on before and after `next()`.

Behaviors are registered per command type at registration time.

## Next Steps

- **[Configure the Command Bus →](../../how-to/cqrs/configure-command-bus.md)** — wiring guide
- **[Pipeline Behaviors →](pipeline-behaviors.md)** — middleware architecture
- **[Unit of Work →](unit-of-work.md)** — transactional scope
- **[Query Bus →](query-bus.md)** — the read-side counterpart
