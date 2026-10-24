# Saga Error Handling

> **Adoption Level:** 5 — Sagas & Process Managers
> **Module:** `pydomain.cqrs.saga.exceptions`
> **Prerequisites:** [Saga](saga.md), [Saga Lifecycle](saga-lifecycle.md), [Saga Compensation](saga-compensation.md)

## Error hierarchy

```
CQRSError
 └── SagaError                    # Base for all saga errors
      ├── SagaStateError          # Invalid state transition
      ├── SagaConfigurationError  # Misconfigured saga (e.g. conflicting flags)
      └── SagaHandlerNotFoundError # No handler registered for an event type
```

## Failure modes

### 1. Event handler failure

When `saga.handle(event)` raises, the `SagaManager._handle_event_error` method kicks in:

```python
try:
    await saga.handle(event)
except Exception as handler_err:
    await self._handle_event_error(saga, state, handler_err)
```

When the saga has compensations to run, the commands the handler queued before it raised are not sent; without compensations they still go out (a known limit, planned for a later release). The error handler then checks the current status:
- **If COMPENSATING:** The saga was already failing — dispatch the compensation commands already queued
- **Otherwise:** Call `saga.fail(describe(handler_err), compensate=True)` — trigger compensation. The reason stored in `state.error` is the exception's type name (`RuntimeError`), never its message, which may quote event or command data ([ADR-071](../../../adr/ADR-071-library-failure-paths-carry-no-payload-values.md)). A saga that needs a specific reason calls `self.fail(reason)` itself with a value-free reason.

The event is marked as processed even on failure, preventing wasteful re-delivery.

### 2. Command dispatch failure

When a command fails during dispatch, the saga is **suspended** (not failed):

```python
except Exception as dispatch_err:
    saga.suspend(reason=f"Dispatch failed: {describe(_failure_of(dispatch_err))}")
    state.retry_count += 1
    await self.repository.save(state)
    raise
```

`_failure_of(dispatch_err)` is the command handler's own exception (the `__cause__` of the bus's `CommandExecutionError`), and the reason names its type only, for example `Dispatch failed: RuntimeError`: its message may quote command data ([ADR-071](../../../adr/ADR-071-library-failure-paths-carry-no-payload-values.md)). If the saga times out while suspended, the default `on_timeout()` records `Saga timed out while suspended for: Dispatch failed: RuntimeError` in `state.error`.

This preserves the saga for recovery — the command might succeed on retry. Each failed attempt adds one to `retry_count`, here and in recovery. `max_retries` is the number of retries allowed after the first attempt: once `retry_count` exceeds it, the next event or recovery fails the saga ([Retry exhaustion](#6-retry-exhaustion)).

### 3. Failure with a forward command (`fail=True`)

A step declared with `fail=True` queues its `send` command and then fails the saga with compensation; so does a handler that calls `self.dispatch(cmd)` and then `await self.fail(reason)`. The manager sends that forward command first, with the step's event as its `causation_id`, then the compensations in LIFO order ([ADR-077](../../../adr/ADR-077-saga-failure-and-recovery-keep-every-command.md)). If the forward command fails, it is recorded in `failed_compensations` by its exception's type, the compensations still run, and the saga ends `FAILED`.

### 4. Unhydratable pending command

Recovery rebuilds each pending command from its stored `module_name` and `command_type`. It sends them in order and stops at the first one it cannot rebuild (the class was renamed or moved): that command and the ones after it are not sent and stay in `pending_commands`, so their order is kept. The attempt counts as failed (`retry_count` goes up once), and an ERROR log names the command type and the saga id only. A saga that was not suspended is suspended with the reason `Could not hydrate <CommandType>`, and recovery marks that suspension as its own with `"suspended_by_recovery": True` on the command's `pending_commands` entry, next to `"dispatched"`. A saga already suspended keeps its own reason and `timeout_at`; recovery never resumes or rewrites a suspension it did not mark, whatever its reason says. Fix the import path, or map the old name, and the next recovery sends the waiting commands and ends the suspension it marked (the saga is `RUNNING` again). While recovery's marked suspension holds the saga, any event resumes it, whatever `resumes_from` or `should_resume()` say, and ends that suspension; those gates apply to your own suspensions only. Otherwise, if `max_retries` runs out first, the saga fails like any saga whose command keeps failing.

### 5. Compensation failure

When a compensation command fails during dispatch, it's recorded rather than halting:

```python
try:
    await self.command_bus.dispatch(traced)
except Exception as comp_err:
    state.record_failed_compensation(..., error=describe(_failure_of(comp_err)))
```

The stored error is the type of the command handler's own exception, the `__cause__` of a bus's `CommandExecutionError`, as for a failed forward command (ADR-081 point 6).

The saga continues compensating remaining steps. After all compensations are processed:
- All succeeded → `COMPENSATED`
- Some failed → `FAILED` with `failed_compensations` populated

### 6. Retry exhaustion

The `SagaManager` checks the retry count before processing any event and before each recovery attempt:

```python
def _retries_exhausted(state: SagaState) -> bool:
    return state.max_retries is not None and state.retry_count > state.max_retries
```

`max_retries` (default 3) is the number of retries after the first attempt: `3` allows three retries, `0` allows none, and `None` allows any number. On an event the saga fails at once with `error = "Retry limit exceeded"`; in recovery it fails with compensation. Once FAILED, the saga is terminal and ignores all future events.

### 7. Missing correlation_id

If an event arrives without a `correlation_id`, the manager logs a warning and skips it:

```python
if not correlation_id:
    logger.warning("Event %s has no correlation_id — cannot route to saga", ...)
    return
```

Sagas require correlation to find or create the right state instance.

### 8. Configuration errors

Detected at registration time (fail-fast):

```python
# Raises SagaConfigurationError:
self.on(EventType, handler=fn, send=lambda e: cmd)  # Cannot provide both
self.on(EventType, complete=True, suspend=True)       # Conflicting flags
```

## Recovery strategies

### Automatic recovery

Call `recover_pending_sagas()` on a schedule (e.g., every 60 seconds):

```python
not_recovered = await manager.recover_pending_sagas(limit=50)
```

This handles:
- Stalled forward commands → re-dispatch
- Stalled compensations → resume
- Retry-exhausted sagas → force-fail

One saga's failure does not stop the batch: it is logged at ERROR by saga id and exception type, and the call returns the ids of the sagas it did not recover.

### Timeout recovery

Call `process_timeouts()` on a schedule:

```python
await manager.process_timeouts(limit=50)
```

Handles suspended sagas whose timeout has expired.

### Manual intervention

For sagas stuck in SUSPENDED with no timeout, use the repository to find and inspect them:

```python
suspended = await repository.find_suspended_sagas(limit=100)
for state in suspended:
    print(f"Saga {state.id} — suspended at {state.suspended_at}: {state.suspension_reason}")
```

## Designing for failure

- **Every forward step should register a compensating command** — even if it seems unlikely to fail
- **Compensation commands must be idempotent** — they may be re-dispatched after a crash
- **Set `max_retries` appropriately** — too low and transient failures kill the saga; too high and it stalls indefinitely
- **Use `suspend_timeout` for human steps** — without a timeout, a suspended saga waiting for human action blocks forever
- **Monitor `SUSPENDED` and `FAILED` counts** — they indicate sagas that need attention

## Next steps

- [How to Handle Saga Errors](../../how-to/sagas/saga-error-handling.md) — implement error recovery
- [How to Suspend, Resume & Timeout](../../how-to/sagas/saga-suspend-resume-timeout.md) — human-in-the-loop patterns
- [Saga Compensation](saga-compensation.md) — compensation in depth
