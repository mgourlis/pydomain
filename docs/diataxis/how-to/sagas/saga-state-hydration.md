# How to Hydrate Saga State

> **Adoption Level:** 5 · Prerequisites: [Saga State concept](../../concepts/sagas/saga-state.md), [Saga Compensation concept](../../concepts/sagas/saga-compensation.md)

Command hydration is the process of reconstructing live `Command` instances from serialized data stored in `CompensationRecord` or `pending_commands`. This guide covers when hydration happens and how to use the hydration API directly.

## When hydration happens

Hydration occurs automatically in two scenarios:

1. **Compensation execution:** `execute_compensations()` pops `CompensationRecord` entries and calls `hydrate_command()` for each
2. **Crash recovery:** `recover_pending_sagas()` hydrates undispatched `pending_commands`

The two react differently to a command that cannot be hydrated:

- **A compensation** is recorded in `state.failed_compensations` with the error `Could not hydrate from <module>`, and the saga ends `FAILED` after the other compensations.
- **A pending forward command** is not sent and stays in `pending_commands`, marked not dispatched. Recovery sends pending commands in order and stops there: the commands before it were sent, the ones after it wait, so they keep their order. Recovery counts the attempt as failed (`retry_count` goes up once) and logs the command type and the saga id at ERROR. A saga that was not suspended is suspended with the reason `Could not hydrate <CommandType>`, and recovery marks that suspension as its own with `"suspended_by_recovery": True` on the command's `pending_commands` entry, next to `"dispatched"`. A saga already suspended keeps its own reason and `timeout_at`; recovery never resumes or rewrites a suspension it did not mark, whatever its reason says. Once the module or class can be imported again, the next `recover_pending_sagas()` sends the waiting commands in order, clears `pending_commands`, and, when the suspension carries recovery's mark, resumes the saga to `RUNNING`. If `max_retries` runs out first, the saga fails as it would for any command that keeps failing ([ADR-077](../../../adr/ADR-077-saga-failure-and-recovery-keep-every-command.md)).

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

A saga suspended with `Could not hydrate <CommandType>` has a pending command whose class cannot be imported. List them:

```python
for entry in state.pending_commands:
    if not entry["dispatched"]:
        print(entry["module_name"], entry["command_type"])
```

Restore the import path (or keep the old module as an alias), and the next recovery run sends the command. `recover_pending_sagas()` returns the saga's id while the command still cannot be hydrated.

`hydrate_command()` logs each failure at `WARNING` on the `pydomain.cqrs.saga.hydration` logger. The line names the module and the command type, then the failure: the exception's type, and for a validation failure the failing fields with Pydantic's error codes. It never contains the stored data's values ([ADR-071](../../../adr/ADR-071-library-failure-paths-carry-no-payload-values.md)):

```text
WARNING  Could not resolve myapp.old.commands.OldCmd: ModuleNotFoundError
WARNING  Validation failed for myapp.commands.RefundPayment: ValidationError: 2 errors for RefundPayment at payment_id (missing), amount (int_parsing)
```

A module or command type that is missing is in the first part of the line. To see the values that failed, load the saga state and validate the record's `data` against the command yourself, under your application's access rules.

Failed compensations are recorded in `state.failed_compensations`:

```python
for failure in state.failed_compensations:
    print(f"Failed to compensate: {failure['command_type']}")
    print(f"  Error: {failure['error']}")
    print(f"  Module: {failure['module_name']}")
```

`error` is the failure's type name (for example `CommandExecutionError` when the command handler raised), or `Could not hydrate from <module>` when the command could not be rebuilt. The exception's message is not stored, because it may quote the command's data. `failed_compensations` and `pending_commands` are left out of `repr(state)`, so logging a saga state does not print the commands' data; read them from the attributes.

## Next steps

- [Implement Compensation](saga-compensation.md) — register and execute compensating actions
- [Handle Saga Errors](saga-error-handling.md) — what happens when hydration fails
