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

The error handler checks the current status:
- **If COMPENSATING:** The saga was already failing — dispatch the compensation commands already queued
- **Otherwise:** Call `saga.fail(str(handler_err), compensate=True)` — trigger compensation

The event is marked as processed even on failure, preventing wasteful re-delivery.

### 2. Command dispatch failure

When a command fails during dispatch, the saga is **suspended** (not failed):

```python
except Exception as dispatch_err:
    saga.suspend(reason=f"Dispatch failed: {cause}")
    state.retry_count += 1
    await self.repository.save(state)
    raise
```

This preserves the saga for recovery — the command might succeed on retry. The `retry_count` is incremented; if it reaches `max_retries`, the saga transitions to FAILED on the next recovery attempt.

### 3. Compensation failure

When a compensation command fails during dispatch, it's recorded rather than halting:

```python
try:
    await self.command_bus.dispatch(traced)
except Exception as comp_err:
    state.record_failed_compensation(...)
```

The saga continues compensating remaining steps. After all compensations are processed:
- All succeeded → `COMPENSATED`
- Some failed → `FAILED` with `failed_compensations` populated

### 4. Retry exhaustion

The `SagaManager` checks retry count before processing any event:

```python
if state.retry_count >= state.max_retries and state.max_retries > 0:
    state.status = SagaStatus.FAILED
    state.error = "Retry limit exceeded"
```

This guard applies to both normal processing and recovery. Once FAILED, the saga is terminal and ignores all future events.

### 5. Missing correlation_id

If an event arrives without a `correlation_id`, the manager logs a warning and skips it:

```python
if not correlation_id:
    logger.warning("Event %s has no correlation_id — cannot route to saga", ...)
    return
```

Sagas require correlation to find or create the right state instance.

### 6. Configuration errors

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
await manager.recover_pending_sagas(limit=50)
```

This handles:
- Stalled forward commands → re-dispatch
- Stalled compensations → resume
- Retry-exhausted sagas → force-fail

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
