# Saga Lifecycle

> **Adoption Level:** 5 — Sagas & Process Managers
> **Prerequisites:** [Saga](saga.md), [Saga State](saga-state.md)

## State machine

A saga progresses through a well-defined lifecycle. The `SagaStatus` enum captures every state:

```
                         ┌──────────┐
                         │ PENDING  │  ← created, waiting for first event
                         └────┬─────┘
                              │ first event arrives
                              ▼
                         ┌──────────┐
              ┌──────────│ RUNNING  │───────────┐
              │          └────┬─────┘           │
              │               │                 │
              │ suspend()     │ complete()      │ fail()
              ▼               ▼                 ▼
     ┌────────────┐  ┌───────────┐  ┌────────────────┐
     │ SUSPENDED  │  │ COMPLETED │  │  COMPENSATING  │
     └──────┬─────┘  └───────────┘  └───────┬────────┘
            │                               │
            │ resume()                dispatch done
            ▼                         ┌───┴────┐
     ┌──────────┐                     ▼        ▼
     │ RUNNING  │             ┌──────────┐ ┌────────┐
     └──────────┘             │COMPENSATED│ │ FAILED │
                              └──────────┘ └────────┘
```

## Transitions in detail

### PENDING → RUNNING

Automatic. The first call to `handle(event)` transitions from PENDING to RUNNING:

```python
if self.state.status == SagaStatus.PENDING:
    self.state.status = SagaStatus.RUNNING
```

### RUNNING → COMPLETED

Explicit. Called via `complete()` or the `complete=True` flag in `on()`:

```python
self.on(ItemsShipped, send=lambda e: NotifyCustomer(...), complete=True)
```

`complete()` also clears the compensation stack — a completed saga has nothing to undo.

### RUNNING → SUSPENDED

Explicit. Called via `suspend()` or the `suspend=True` flag:

```python
self.on(PaymentConfirmed,
        send=lambda e: RequestApproval(order_id=e.order_id),
        suspend=True,
        suspend_reason="Awaiting manager approval for large order",
        suspend_timeout=timedelta(hours=48))
```

Sets `status = SUSPENDED`, records `suspended_at`, `suspension_reason`, and optionally `timeout_at`.

### SUSPENDED → RUNNING

Controlled by `should_resume()`:

```python
def should_resume(self, event: DomainEvent) -> bool:
    # Base logic: check resumes_from and should_resume predicates
    ...
```

The `SagaManager` calls `should_resume()` on each incoming event for suspended sagas. If it returns `True`, `resume()` is called, clearing suspension fields and returning to RUNNING.

Step-based authorization via `resumes_from`:

```python
self.on(ManagerApproved,
        send=lambda e: ShipItems(order_id=e.order_id),
        step="shipping",
        resumes_from="awaiting_approval")  # Only resume if we're at this step
```

Inline predicates via `should_resume`:

```python
self.on(PaymentConfirmed,
        handler=self.handle_payment,
        should_resume=lambda e: e.amount > Money(amount=100, currency="EUR"))
```

### RUNNING → COMPENSATING → COMPENSATED / FAILED

Triggered by `fail(reason, compensate=True)`:

```python
await saga.fail("Payment gateway timeout", compensate=True)
```

Sets `status = COMPENSATING`, drains the compensation stack LIFO. After all commands dispatch: `COMPENSATED` (all succeeded) or `FAILED` (some failed).

The `fail=True` flag in `on()` provides the same behavior inline:

```python
self.on(PaymentFailed,
        send=lambda e: MarkOrderFailed(order_id=e.order_id),
        fail=True,
        fail_reason="Payment was declined")
```

### FAILED

Terminal. `fail()` with `compensate=False`, or after compensation dispatch failures, or when `retry_count >= max_retries` on recovery.

### Force-fail on retry exhaustion

The `SagaManager` enforces a hard guard before processing any event:

```python
if state.retry_count >= state.max_retries and state.max_retries > 0:
    state.status = SagaStatus.FAILED
    state.error = "Retry limit exceeded"
```

## Timeout handling

Suspended sagas with `timeout_at` set are eligible for timeout processing. The `SagaManager.process_timeouts()` method:

1. Finds all expired suspended sagas
2. Calls `saga.on_timeout()` — a hook subclasses can override
3. If still SUSPENDED after the hook, force-fails with `compensate=False`
4. Dispatches any commands queued during the timeout handler

Default `on_timeout()` behavior: fail with a message including the suspension reason.

## Terminal states

Sagas in `COMPLETED`, `FAILED`, or `COMPENSATED` are terminal. The `is_terminal` property gates all event processing:

```python
if self.state.is_terminal:
    return  # Ignore all events
```

## Next steps

- [Saga Error Handling](saga-error-handling.md) — failure modes in depth
- [How to Suspend, Resume & Timeout](../../how-to/sagas/saga-suspend-resume-timeout.md) — human-in-the-loop patterns
