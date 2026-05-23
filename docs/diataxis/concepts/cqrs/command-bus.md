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
  ├── 4. Run pipeline (behaviors → terminal handler)
  ├── 5. On success: commit UoW (stamps events internally), collect events
  ├── 6. On failure: rollback UoW, raise CommandExecutionError
  └── 7. Return (result, events) tuple
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

1. **UoW created** via the registered factory — fresh per dispatch
2. **Pipeline executes** — behaviors wrap the handler in onion order
3. **On success:** `uow.commit()` persists changes and collects events
4. **On failure:** `uow.rollback()` undoes changes, then `CommandExecutionError` wraps the original exception

The handler never calls `commit()` or `rollback()` — the bus owns the transaction boundary.

## Tracing ID Propagation

The bus resolves tracing IDs from the command:

```python
correlation_id = command.correlation_id or command.command_id
causation_id = command.causation_id or command.command_id
```

When a [Saga](../sagas/saga.md) dispatches a command with explicit tracing IDs, those are used. For direct dispatches, `command_id` serves as both. The resolved IDs are stamped onto the UoW so events collected during `commit()` carry the correct chain.

## Pipeline Behaviors

The bus wraps each handler in a [Pipeline](pipeline-behaviors.md) that executes behaviors in onion order before the terminal handler:

```
Outermost behavior
  └── Middle behavior
        └── Innermost behavior
              └── Terminal handler (your code)
```

Behaviors are registered per command type at registration time.

## Next Steps

- **[Configure the Command Bus →](../../how-to/cqrs/configure-command-bus.md)** — wiring guide
- **[Pipeline Behaviors →](pipeline-behaviors.md)** — middleware architecture
- **[Unit of Work →](unit-of-work.md)** — transactional scope
- **[Query Bus →](query-bus.md)** — the read-side counterpart
