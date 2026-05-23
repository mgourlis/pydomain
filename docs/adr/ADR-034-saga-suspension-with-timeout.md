# ADR-034: Saga Suspension with Timeout (Human-in-the-Loop)

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Some business processes require human approval or external events that may take hours or days. A saga that books a flight and then waits for payment confirmation cannot run synchronously — it must suspend and resume later when the external event arrives.

The saga needs to:
1. Suspend at a specific step.
2. Persist its state (for crash recovery).
3. Optionally set a timeout (auto-fail if no resume event arrives).
4. Resume when the expected event arrives (or timeout triggers).

## Decision

Sagas support suspension and resumption through lifecycle methods:

```python
class Saga[S: SagaState]:
    def suspend(self, reason: str, timeout: timedelta | None = None):
        self.state.status = SagaStatus.SUSPENDED
        self.state.suspended_at = datetime.now(UTC)
        self.state.suspension_reason = reason
        if timeout is not None:
            self.state.timeout_at = datetime.now(UTC) + timeout

    def resume(self):
        self.state.status = SagaStatus.RUNNING
        self.state.suspended_at = None
        self.state.suspension_reason = None
        self.state.timeout_at = None

    def should_resume(self, event: DomainEvent) -> bool:
        # Override in subclasses to filter which events can resume
        return True

    async def on_timeout(self):
        # Override for custom recovery
        await self.fail("Saga timed out while suspended")
```

**Declaration via `on()` DSL**:

```python
self.on(PaymentRequested,
    send=lambda e: ReservePayment(order_id=e.order_id),
    step="waiting_payment",
    suspend=True,
    suspend_reason="Waiting for payment confirmation",
    suspend_timeout=timedelta(hours=24))
```

**Timeout handling**: The `SagaManager` checks `timeout_at` on loaded sagas. If the current time exceeds the timeout, `on_timeout()` is called (defaults to `fail()`, override for custom recovery).

**Resume filtering**: `should_resume(event)` allows subclasses to restrict which events can resume a suspended saga. Default returns `True` for all events.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| No suspension (blocking wait) | Blocks the event loop; impractical for hours/days; no crash recovery |
| Separate "wait" state machine | Duplicates saga lifecycle; adds complexity; no unified state tracking |
| External workflow engine | Overkill for simple human-approval scenarios; additional infrastructure dependency |

## Consequences

### Positive

- Sagas can wait for external events without blocking — true long-running process support.
- Timeout ensures sagas don't hang forever — `on_timeout()` provides a recovery hook.
- Suspension is declarative — `suspend=True` on the `on()` DSL captures the intent.
- Resume filtering prevents irrelevant events from waking a suspended saga.

### Negative

- The saga manager must periodically check for timed-out sagas (polling or scheduled task).
- Timeout handling is synchronous — `on_timeout()` is async but runs in the manager's event loop.

### Neutral

- Suspension metadata (`suspended_at`, `suspension_reason`, `timeout_at`) is persisted in `SagaState` for audit and recovery.

## References

- `src/pydomain/cqrs/saga/saga.py` — `suspend()`, `resume()`, `should_resume()`, `on_timeout()`
- `src/pydomain/cqrs/saga/state.py` — `SagaStatus.SUSPENDED`, `suspended_at`, `timeout_at`
