# How to Handle Saga Errors

> **Adoption Level:** 5 · Prerequisites: [Saga Error Handling concept](../../concepts/sagas/saga-error-handling.md), [Saga Compensation concept](../../concepts/sagas/saga-compensation.md)

This guide covers practical error handling: configuring retries, recovering stalled sagas, and monitoring.

## 1. Configure max retries

Set the default of the `max_retries` field on your state class to control how many retries are allowed after the first dispatch attempt before the saga is force-failed:

```python
from pydantic import Field


class OrderFulfillmentState(SagaState):
    max_retries: int | None = Field(default=5, ge=0)  # the first attempt, then up to 5 retries


class OrderFulfillmentSaga(Saga[OrderFulfillmentState]):
    state_class = OrderFulfillmentState
    listens_to = [...]
```

`Field` comes from `pydantic`; keeping `ge=0` keeps the base class's check that refuses a negative value with `ValidationError`. The default is `3`: three retries after the first attempt. `0` allows no retries (the first failure is final), and `None` allows any number, so the saga is never failed for retry exhaustion. Both the event path and recovery use the same rule: the saga fails once `retry_count > max_retries` ([ADR-077](../../../adr/ADR-077-saga-failure-and-recovery-keep-every-command.md)).

> **Changed in 0.3.1:** `0` used to mean "unlimited" on the event path and "fail at once" in recovery, and `3` allowed fewer retries than three. Use `None` for unlimited ([upgrade notes](../upgrade/upgrade-to-0.3.1.md)).

## 2. Handle dispatch failures gracefully

Dispatch failures suspend the saga rather than failing it:

```python
# In SagaManager._dispatch_forward_commands:
try:
    await self._dispatch_and_persist_commands(state, traced_commands)
except Exception as dispatch_err:
    # The handler's own exception, described by its type only (ADR-071)
    saga.suspend(reason=f"Dispatch failed: {describe(_failure_of(dispatch_err))}")
    state.retry_count += 1
    await self.repository.save(state)
    raise
```

This means the saga survives transient infrastructure failures (broker down, network blip). The recovery loop will retry.

## 3. Schedule recovery

Run `recover_pending_sagas()` on a timer:

```python
import asyncio


async def saga_recovery_loop(manager: SagaManager, interval: int = 30):
    """Recover stalled sagas every 30 seconds."""
    while True:
        await asyncio.sleep(interval)
        not_recovered = await manager.recover_pending_sagas(limit=50)
        if not_recovered:
            logger.warning("%d sagas not recovered this run", len(not_recovered))
```

This re-dispatches commands that were persisted but not confirmed, resumes stalled compensations, and force-fails sagas that exhausted retries. A saga whose recovery fails does not stop the others: the manager logs it at ERROR (saga id and exception type) and moves on, and the call returns the ids of the sagas it did not recover, so the loop needs no `try` for them. A saga whose pending command can no longer be imported is suspended with the reason `Could not hydrate <CommandType>`, marked as recovery's own on the entry (or keeps an earlier suspension, which recovery never resumes), and the commands after that one wait; it is in that list on every run until the import path is fixed or its retries run out. Once fixed, recovery sends the waiting commands in order and ends the suspension it marked.

## 4. Handle timeouts

Schedule `process_timeouts()` alongside recovery:

```python
async def saga_maintenance_loop(manager: SagaManager, interval: int = 30):
    while True:
        await asyncio.sleep(interval)
        await manager.recover_pending_sagas(limit=50)
        try:
            await manager.process_timeouts(limit=50)
        except Exception:
            logger.exception("Saga timeout processing failed")
```

`recover_pending_sagas()` reports the sagas it could not recover instead of raising, so `process_timeouts()` runs on every pass. An error from the repository itself (the stalled-saga query) still propagates; let it stop the loop or catch it as you would any infrastructure error.

## 5. Monitor saga health

Expose key metrics from the repository:

```python
async def saga_health_check(repo: SagaRepository) -> dict:
    stalled = await repo.find_stalled_sagas(limit=100)
    suspended = await repo.find_suspended_sagas(limit=100)
    expired = await repo.find_expired_suspended_sagas(limit=100)

    return {
        "stalled_count": len(stalled),
        "suspended_count": len(suspended),
        "expired_suspended_count": len(expired),
        "needs_attention": [
            {"id": str(s.id), "saga_type": s.saga_type, "status": s.status.value, "error": s.error}
            for s in stalled + expired
            if s.status == SagaStatus.FAILED
        ],
    }
```

Set up alerts for:
- **Non-zero FAILED count** — sagas that couldn't recover automatically
- **Growing SUSPENDED count** — may indicate a missing event or handler
- **Growing stalled count** — may indicate a dispatch infrastructure problem

## 6. Manually intervene on stuck sagas

For sagas that require manual action:

```python
async def manually_fail_saga(repo: SagaRepository, saga_id: UUID, reason: str):
    """Force-fail a stuck saga."""
    state = await repo.get_by_id(saga_id)
    if state is None:
        raise ValueError(f"Saga {saga_id} not found")

    saga_class = registry.get_saga_type(state.saga_type)
    saga = saga_class(state)
    await saga.fail(reason, compensate=True)

    # If compensation was triggered, dispatch those commands
    if state.status == SagaStatus.COMPENSATING:
        commands = saga.collect_commands()
        for cmd in commands:
            await message_bus.dispatch(cmd)

    await repo.save(state)
```

## 7. Log errors with context

The saga infrastructure logs at key points — ensure your logging captures:

- **Saga handler failures:** `logger.exception("Saga %s failed for event %s", ...)`
- **Dispatch failures:** `logger.error("Saga %s stalled during dispatch: %s", ...)`, with the exception's type
- **Recovery failures:** `logger.error("Recovery failed for saga %s: %s", ...)`, with the exception's type
- **Unhydratable pending commands:** `logger.error("Could not hydrate command %s for saga %s; it and the commands after it stay pending", ...)`
- **Failed forward commands during compensation:** `logger.error("Forward command %s failed during compensation of saga %s: %s", ...)`, with the exception's type
- **Compensation failures:** `logger.error("Compensation command %s failed for saga %s: %s", ...)`
- **Timeout handler failures:** `logger.error("on_timeout() failed for saga %s: %s", ...)`

Include `saga_id`, `saga_type`, and `correlation_id` in log context for traceability.

The errors the manager stores are value-free: `state.error` and `failed_compensations[...]["error"]` hold the exception's type name, not its message ([ADR-071](../../../adr/ADR-071-library-failure-paths-carry-no-payload-values.md)). The dispatch, recovery and hydration lines name the exception's type only; the handler, compensation and timeout lines still format the exception's message. So does the `Saga <name> failed for event <name>` record that `SagaManager.handle()` writes with `logger.exception` when processing a saga raises, whatever the cause, a failed dispatch included: its traceback carries the exception's message. Handler-failure logs are planned to change in a later release; if your command handlers' exceptions can quote personal data, see [Handle Sensitive Payloads](../infrastructure/handle-sensitive-payloads.md).

## Next steps

- [Saga Error Handling concept](../../concepts/sagas/saga-error-handling.md) — architecture deep dive
- [Configure a Saga Manager](configure-saga-manager.md) — full wiring with recovery loops
- [Prune Saga History](saga-pruning.md) — prevent unbounded growth in long-lived sagas
