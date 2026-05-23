# How to Hydrate Saga State

> **Adoption Level:** 5 · Prerequisites: [Saga State concept](../../concepts/sagas/saga-state.md), [Saga Compensation concept](../../concepts/sagas/saga-compensation.md)

Command hydration is the process of reconstructing live `Command` instances from serialized data stored in `CompensationRecord` or `pending_commands`. This guide covers when hydration happens and how to use the hydration API directly.

## When hydration happens

Hydration occurs automatically in two scenarios:

1. **Compensation execution:** `execute_compensations()` pops `CompensationRecord` entries and calls `hydrate_command()` for each
2. **Crash recovery:** `recover_pending_sagas()` hydrates undispatched `pending_commands`

You don't normally call `hydrate_command()` directly — but understanding it helps debug compensation failures.

## The `hydrate_command` function

```python
from pydomain.cqrs.saga import hydrate_command

command = hydrate_command(
    module_name="myapp.orders.commands",
    command_type="CancelReservation",
    data={"order_id": UUID("019345b8-...")},
)
# → CancelReservation(order_id=UUID("019345b8-..."))
```

### How it works

1. Import the module by name via `importlib.import_module()`
2. Look up the command class via `getattr(mod, command_type)`
3. Filter the data dict to only include fields known to the model
4. Call `cls.model_validate(filtered)` to reconstruct the instance

### Resilience to schema evolution

Unknown keys in `data` are **stripped** before validation. This means a compensation record stored when the command had an extra field won't break if that field is later removed — the write side already validated it when the command was first created.

### Return value

- Returns a `Command` instance on success
- Returns `None` if the module or class cannot be resolved (e.g., command from a different service, renamed module)
- Returns `None` on validation failure

## Hydrating custom commands

```python
from pydomain.cqrs.saga import hydrate_command

# Success — module and class are importable
cmd = hydrate_command(
    module_name="myapp.orders.commands",
    command_type="CancelReservation",
    data={"order_id": UUID("..."), "reason": "customer_request"},
)
assert cmd is not None

# None — module doesn't exist
cmd = hydrate_command(
    module_name="deleted_service.commands",
    command_type="OldCommand",
    data={},
)
assert cmd is None

# None — class doesn't exist in module
cmd = hydrate_command(
    module_name="myapp.orders.commands",
    command_type="RemovedCommand",
    data={},
)
assert cmd is None
```

## Debugging hydration failures

The `SagaManager` and `saga.execute_compensations()` both log failures:

```
WARNING  Could not resolve myapp.old.commands.OldCmd: No module named 'myapp.old'
WARNING  Validation failed for myapp.commands.BrokenCmd: 1 validation error
```

Failed compensations are recorded in `state.failed_compensations`:

```python
for failure in state.failed_compensations:
    print(f"Failed to compensate: {failure['command_type']}")
    print(f"  Error: {failure['error']}")
    print(f"  Module: {failure['module_name']}")
```

## Next steps

- [Implement Compensation](saga-compensation.md) — register and execute compensating actions
- [Handle Saga Errors](saga-error-handling.md) — what happens when hydration fails
