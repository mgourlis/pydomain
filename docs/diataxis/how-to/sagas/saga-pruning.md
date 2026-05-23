# How to Prune Saga History

> **Adoption Level:** 5 · Prerequisites: [Saga State concept](../../concepts/sagas/saga-state.md), [Define a Saga](define-saga.md)

Long-lived sagas accumulate unbounded history — step records and processed event IDs. This guide shows how to cap growth with class-level limits and automated pruning policies.

## 1. Cap with class-level limits

The simplest approach: set `max_processed_events` and `max_step_history` on the state class:

```python
from typing import ClassVar
from pydomain.cqrs.saga.state import SagaState


class OrderFulfillmentState(SagaState):
    max_processed_events: ClassVar[int] = 500   # Keep last 500 event IDs
    max_step_history: ClassVar[int] = 100       # Keep last 100 step records
```

When limits are exceeded, the oldest entries are discarded automatically during `mark_event_processed()` and `record_step()`. Set to `0` (default) for unlimited storage.

## 2. Use a pruning policy for threshold-based pruning

For more control, attach a `pruning_policy` to the state class:

```python
from pydomain.cqrs.saga import StepThresholdPruningPolicy


class OrderFulfillmentState(SagaState):
    pruning_policy: ClassVar[SagaPruningPolicy | None] = StepThresholdPruningPolicy(
        step_threshold=50,       # Prune when step_history reaches 50
        keep_last_n_steps=10,    # Keep the most recent 10 steps
        keep_last_n_events=100,  # Keep the most recent 100 event IDs
    )
```

The policy is evaluated after every `record_step()` call (which happens at the end of each `handle()` invocation).

### Safety guards

The policy **never** recommends pruning for sagas in these states:
- `COMPENSATING` — compensation stack integrity must be preserved
- `SUSPENDED` — full history may be needed on resume
- `COMPLETED`, `FAILED`, `COMPENSATED` — terminal states, pruning provides no benefit

### `step_threshold = 0`

Setting `step_threshold=0` triggers pruning on every evaluation:

```python
StepThresholdPruningPolicy(step_threshold=0, keep_last_n_steps=10)
```

This prunes after every single step, keeping only the last 10. Use cautiously — it increases overhead for frequently-stepping sagas.

## 3. Manual pruning

Call `prune_history()` explicitly for one-off cleanup:

```python
# Keep the last 20 step records and 200 event IDs
state.prune_history(keep_last_n_steps=20, keep_last_n_events=200)

# Clear all history
state.prune_history(keep_last_n_steps=0, keep_last_n_events=0)

# Only prune steps, leave events alone
state.prune_history(keep_last_n_steps=10)  # keep_last_n_events defaults to None
```

Use this for batch cleanup jobs or before long-term archival.

## 4. Custom pruning policy

Implement the `SagaPruningPolicy` protocol for custom logic:

```python
from pydomain.cqrs.saga.pruning import SagaPruningPolicy


class TimeBasedPruningPolicy(SagaPruningPolicy):
    """Prune step history older than a configured age."""

    def __init__(self, keep_last_n_steps: int = 10, max_age_hours: int = 24):
        self._keep_last_n_steps = keep_last_n_steps
        self._max_age_hours = max_age_hours

    @property
    def keep_last_n_steps(self) -> int:
        return self._keep_last_n_steps

    @property
    def keep_last_n_events(self) -> int | None:
        return None  # Don't prune event IDs

    def should_prune(self, saga_type: str, state: SagaState) -> bool:
        from pydomain.cqrs.saga.state import SagaStatus

        # Respect the safety guard (no pruning in critical states)
        if state.status.value in {"COMPENSATING", "SUSPENDED"}:
            return False

        # Prune if any step is older than max_age
        cutoff = datetime.now(UTC) - timedelta(hours=self._max_age_hours)
        return any(s.occurred_at < cutoff for s in state.step_history)
```

## 5. Pruning and compensation stack

The compensation stack is **not** affected by pruning. Pruning only targets:
- `step_history` — step records
- `processed_event_ids` — idempotency tracking

The compensation stack is cleared by `complete()` or drained by `execute_compensations()` — never by pruning.

## 6. Test pruning

```python
@pytest.mark.anyio
async def test_step_threshold_pruning():
    state = OrderFulfillmentState(
        id=uuid4(),
        saga_type="TestSaga",
    )

    # Simulate many steps
    for i in range(60):
        state.record_step(step_name=f"step_{i}", event_type="TestEvent")

    # With step_threshold=50, pruning should have triggered
    # and we should have at most keep_last_n_steps=10
    assert len(state.step_history) <= 10
```

## Next steps

- [Saga State concept](../../concepts/sagas/saga-state.md) — all state fields and memory bounds
- [Configure a Saga Manager](configure-saga-manager.md) — full application wiring
