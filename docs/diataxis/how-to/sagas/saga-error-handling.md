# How to Handle Saga Errors

> **Adoption Level:** 5 · Prerequisites: [Saga Error Handling concept](../../concepts/sagas/saga-error-handling.md), [Saga Compensation concept](../../concepts/sagas/saga-compensation.md)

This guide covers practical error handling: configuring retries, recovering stalled sagas, and monitoring.

## 1. Configure max retries

Set `max_retries` on the state class to control how many dispatch attempts are allowed before the saga is force-failed:

```python
class OrderFulfillmentState(SagaState):
    max_retries: ClassVar[int] = 5  # Allow up to 5 retry attempts


class OrderFulfillmentSaga(Saga[OrderFulfillmentState]):
    state_class = OrderFulfillmentState
    listens_to = [...]
```

Default is `3`. Set to `0` to disable the retry guard (saga will never be auto-failed for retry exhaustion).

## 2. Handle dispatch failures gracefully

Dispatch failures suspend the saga rather than failing it:

```python
# In SagaManager._dispatch_forward_commands:
try:
    await self._dispatch_and_persist_commands(state, traced_commands)
except Exception as dispatch_err:
    saga.suspend(reason=f"Dispatch failed: {cause}")
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
        try:
            await manager.recover_pending_sagas(limit=50)
        except Exception as exc:
            logger.error("Saga recovery failed: %s", exc)
```

This re-dispatches commands that were persisted but not confirmed, resumes stalled compensations, and force-fails sagas that exhausted retries.

## 4. Handle timeouts

Schedule `process_timeouts()` alongside recovery:

```python
async def saga_maintenance_loop(manager: SagaManager, interval: int = 30):
    while True:
        await asyncio.sleep(interval)
        try:
            await manager.recover_pending_sagas(limit=50)
            await manager.process_timeouts(limit=50)
        except Exception as exc:
            logger.error("Saga maintenance failed: %s", exc)
```

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
            await command_bus.dispatch(cmd)

    await repo.save(state)
```

## 7. Log errors with context

The saga infrastructure logs at key points — ensure your logging captures:

- **Saga handler failures:** `logger.exception("Saga %s failed for event %s", ...)`
- **Dispatch failures:** `logger.error("Saga %s stalled during dispatch: %s", ...)`
- **Compensation failures:** `logger.error("Compensation command %s failed for saga %s: %s", ...)`
- **Timeout handler failures:** `logger.error("on_timeout() failed for saga %s: %s", ...)`

Include `saga_id`, `saga_type`, and `correlation_id` in log context for traceability.

## Next steps

- [Saga Error Handling concept](../../concepts/sagas/saga-error-handling.md) — architecture deep dive
- [Configure a Saga Manager](configure-saga-manager.md) — full wiring with recovery loops
- [Prune Saga History](saga-pruning.md) — prevent unbounded growth in long-lived sagas
