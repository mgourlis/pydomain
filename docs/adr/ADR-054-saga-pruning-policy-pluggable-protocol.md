# ADR-054: Saga Pruning Policy as Pluggable Protocol

## Status

Accepted

## Date

2026-05-22

## Context

Long-lived sagas accumulate step history, processed event IDs, and compensation records. Without bounds, `SagaState` grows unboundedly, increasing storage size, replay time, and memory consumption (§11.3).

The library already provides two manual mechanisms for controlling saga state growth:

1. **`max_processed_events` / `max_step_history` ClassVar caps** — automatic enforcement inside `mark_event_processed()` and `record_step()`.
2. **`prune_history()` method** — explicit pruning callable from a maintenance endpoint or scheduled task.

Both mechanisms require **user intervention**: either subclassing `SagaState` to set `ClassVar` caps, or calling `prune_history()` at the right time. There is no automated, policy-driven pruning analogous to `SnapshotPolicy` (ADR-043) for event-sourced aggregates.

The risk table in §11.3 identifies this gap:

> | No policy-based pruning | Open | A `SagaPruningPolicy` (analogous to `SnapshotPolicy`) could automate when and how to prune. |

Additionally, the risk table identifies a safety concern:

> | Compensation stack integrity after pruning | Open | Pruning should never remove compensation records for steps that haven't completed. A safety guard is needed. |

## Decision

Introduce a `SagaPruningPolicy` Protocol with a concrete `StepThresholdPruningPolicy` implementation, following the same pattern as `SnapshotPolicy` (ADR-043).

### 1. `SagaPruningPolicy` Protocol

```python
@runtime_checkable
class SagaPruningPolicy(Protocol):
    def should_prune(self, saga_type: str, state: SagaState) -> bool: ...
```

The protocol receives the saga type name and current state, and returns `True` if pruning is recommended. The `saga_type` parameter is available for custom policies that differentiate by type (e.g., prune `OrderSaga` but never prune `PaymentSaga`).

### 2. `StepThresholdPruningPolicy` concrete implementation

```python
class StepThresholdPruningPolicy(SagaPruningPolicy):
    def __init__(
        self,
        step_threshold: int = 50,
        keep_last_n_steps: int = 10,
        keep_last_n_events: int | None = None,
    ) -> None: ...

    @property
    def step_threshold(self) -> int: ...
    @property
    def keep_last_n_steps(self) -> int: ...
    @property
    def keep_last_n_events(self) -> int | None: ...

    def should_prune(self, saga_type: str, state: SagaState) -> bool: ...
```

**Threshold logic**:
- `step_threshold > 0`: prune when `len(state.step_history) >= step_threshold`.
- `step_threshold == 0`: prune on every evaluation (for RUNNING/PENDING sagas with at least one step).

**Configuration exposed as read-only properties**: `step_threshold`, `keep_last_n_steps`, and `keep_last_n_events` are accessible so that the `SagaManager` (or any integration point) can pass them to `state.prune_history()`.

### 3. Safety guards

The policy **never** recommends pruning for sagas in the following states:

| Status | Reason |
|--------|--------|
| `COMPENSATING` | Compensation stack integrity depends on step history. Pruning could lose compensation records. |
| `SUSPENDED` | May need full history on resume for debugging or decision-making. |
| `COMPLETED` | Terminal state — no benefit to pruning; audit trail should be preserved. |
| `FAILED` | Terminal state — audit trail should be preserved. |
| `COMPENSATED` | Terminal state — audit trail should be preserved. |

This addresses the "Compensation stack integrity after pruning" risk from §11.3.

### 4. Auto-pruning integration via `SagaState.pruning_policy` ClassVar

The policy is wired into `SagaState` as an optional `ClassVar`, following the same pattern as `max_processed_events` and `max_step_history`:

```python
class SagaState(AggregateRoot[UUID]):
    # Existing memory-bounds config
    max_processed_events: ClassVar[int] = 0
    max_step_history: ClassVar[int] = 0

    # Pruning policy — set on subclass to enable auto-pruning
    pruning_policy: ClassVar[SagaPruningPolicy | None] = None
```

When `pruning_policy` is set on a subclass, `record_step()` automatically evaluates it after appending the step and enforcing `max_step_history`. If the policy recommends pruning, `prune_history()` is called with the policy's `keep_last_n_steps` and `keep_last_n_events` configuration.

**Usage:**

```python
class OrderSagaState(SagaState):
    pruning_policy = StepThresholdPruningPolicy(
        step_threshold=100,
        keep_last_n_steps=20,
        keep_last_n_events=50,
    )
```

No external integration is required — the pruning happens automatically inside `record_step()`. The `SagaManager` does not need to know about the policy.

### 5. Module location

The policy lives in `pydomain.cqrs.saga.pruning` — within the CQRS/saga module, **not** in `pydomain.es`. This respects the architecture constraint that `cqrs` must not import from `es` (§10.2, `test_cqrs_does_not_import_es`).

`state.py` imports `SagaPruningPolicy` only under `TYPE_CHECKING` to avoid circular imports (since `pruning.py` imports `SagaState` under `TYPE_CHECKING` as well).

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Hardcode pruning in `SagaManager` | Couples pruning strategy to orchestrator; cannot customize per saga type. |
| Standalone module without ClassVar wiring | Requires external integration (e.g., `SagaManager`), adding coupling. The ClassVar pattern keeps pruning self-contained within `SagaState`. |
| Return `PruningDecision` dataclass instead of `bool` | Over-engineering for the current need. A bool + config properties is simpler and matches the `SnapshotPolicy` precedent. |
| Time-based pruning (every N seconds) | Does not correlate with step accumulation; may prune unchanged sagas or miss rapidly growing ones. |
| Prune only processed events, not steps | Step history is the primary growth driver; events are secondary. A step-based threshold is more directly correlated with state size. |

## Consequences

### Positive

- **Pluggable**: any object matching the `SagaPruningPolicy` Protocol is a valid policy. Custom policies can consider saga type, age, or any state attribute.
- **Self-contained auto-pruning**: the `pruning_policy` ClassVar on `SagaState` fires automatically from `record_step()`, following the same pattern as `max_processed_events` / `max_step_history`. No external integration required.
- **Safety-first**: built-in `StepThresholdPruningPolicy` refuses to prune sagas in COMPENSATING, SUSPENDED, or terminal states.
- **Consistent with existing patterns**: mirrors both `SnapshotPolicy` (ADR-043) for the Protocol shape, and the `max_*` ClassVar pattern for automatic enforcement.
- **Backward compatible**: the policy is entirely opt-in. `SagaState.pruning_policy` defaults to `None`. Existing code works unchanged.
- **Addresses both risk items from §11.3**: automates pruning decisions and adds safety guards for compensation integrity.

### Negative

- **Pruning vs. idempotency tradeoff remains**: once `processed_event_ids` are pruned, a duplicate event delivery cannot be detected as a duplicate. This is documented in §11.3 and is inherent to pruning.
- **TYPE_CHECKING guard required**: `state.py` imports `SagaPruningPolicy` only under `TYPE_CHECKING` to avoid circular imports with `pruning.py`. The annotation is a string at runtime (thanks to `from __future__ import annotations`), so this is safe but worth noting.

### Neutral

- The `saga_type` parameter is unused by `StepThresholdPruningPolicy` but available for custom implementations that differentiate by saga type.
- The policy is evaluated synchronously (no I/O). This is consistent with the `SnapshotPolicy` pattern.

## References

- `src/pydomain/cqrs/saga/pruning.py` — `SagaPruningPolicy`, `StepThresholdPruningPolicy`
- `src/pydomain/cqrs/saga/state.py` — `SagaState.prune_history()`, memory bounds
- `docs/adr/ADR-043-snapshot-policy-pluggable-protocol.md` — analogous Snapshot Policy
- `docs/arch42/11-risks.md` — §11.3 Saga State Growth and Compensation Stack
- `tests/saga/test_pruning_policy.py` — 45 tests covering the full feature (35 protocol/implementation + 10 ClassVar auto-pruning)
