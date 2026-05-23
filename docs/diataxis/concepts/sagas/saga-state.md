# Saga State

> **Adoption Level:** 5 — Sagas & Process Managers
> **Module:** `pydomain.cqrs.saga.state`
> **Prerequisites:** [Saga](saga.md)

## What is SagaState?

`SagaState` is the persistent aggregate root that tracks every dimension of a saga instance: identity, lifecycle status, step history, idempotency, compensation stack, suspension state, retries, and audit timestamps.

```python
from pydomain.cqrs.saga.state import SagaState, SagaStatus

state = SagaState(
    id=UUID("..."),
    saga_type="OrderFulfillmentSaga",
    correlation_id=order_correlation_id,
)
```

## Key fields

### Identity & lifecycle

| Field | Type | Purpose |
|-------|------|---------|
| `id` | `UUID` | Saga instance identity |
| `saga_type` | `str` | Saga class name (for recovery lookups) |
| `status` | `SagaStatus` | Current lifecycle state |
| `current_step` | `str` | Human-readable step name |

### Step tracking

| Field | Type | Purpose |
|-------|------|---------|
| `step_history` | `list[StepRecord]` | Immutable record of each transition |

Each `StepRecord` captures the step name, event type, causation ID, timestamp, and optional metadata.

### Idempotency

| Field | Type | Purpose |
|-------|------|---------|
| `processed_event_ids` | `set[UUID]` | Events already handled (O(1) lookup) |

Serialized as a `list[UUID]` for JSON/DB compatibility, coerced back to `set` on load.

### Compensation

| Field | Type | Purpose |
|-------|------|---------|
| `compensation_stack` | `list[CompensationRecord]` | LIFO stack of compensating commands |
| `failed_compensations` | `list[dict]` | Audit trail of compensations that couldn't execute |

### Suspension

| Field | Type | Purpose |
|-------|------|---------|
| `suspended_at` | `datetime \| None` | When the saga was suspended |
| `suspension_reason` | `str \| None` | Why (for audit) |
| `timeout_at` | `datetime \| None` | When the suspension expires |

### Retries & errors

| Field | Type | Purpose |
|-------|------|---------|
| `retry_count` | `int` | How many times dispatch has been retried |
| `max_retries` | `int` | Limit before forced failure (default 3) |
| `error` | `str \| None` | Last error message |

### Tracing

| Field | Type | Purpose |
|-------|------|---------|
| `correlation_id` | `UUID \| None` | Links all events in this business process |
| `causation_id` | `UUID \| None` | Last event that caused a state change |

### Audit

| Field | Type | Purpose |
|-------|------|---------|
| `created_at` | `datetime` | When the saga was first persisted |
| `updated_at` | `datetime` | Last mutation timestamp |
| `version` | `int` | Optimistic concurrency token (inherited from `AggregateRoot`) |

### Context

| Field | Type | Purpose |
|-------|------|---------|
| `metadata` | `dict[str, Any]` | Arbitrary key-value store for saga-specific data |

## Memory bounds

Long-lived sagas can accumulate unbounded history. Control growth with class-level limits:

```python
class OrderState(SagaState):
    max_processed_events: ClassVar[int] = 500   # Cap event IDs
    max_step_history: ClassVar[int] = 100       # Cap step records
```

Set to `0` (the default) for unlimited storage. When caps are exceeded, the oldest entries are discarded.

For automated pruning based on thresholds, see [Saga Pruning](../../how-to/sagas/saga-pruning.md) and the `pruning_policy` class variable.

## `SagaStatus` enum

```python
class SagaStatus(StrEnum):
    PENDING = "PENDING"          # Created, waiting for first event
    RUNNING = "RUNNING"          # Actively processing
    SUSPENDED = "SUSPENDED"      # Awaiting external action
    COMPLETED = "COMPLETED"      # Successful terminal state
    FAILED = "FAILED"            # Failed terminal state
    COMPENSATING = "COMPENSATING"  # Executing compensation
    COMPENSATED = "COMPENSATED"  # Compensation complete
```

## `is_terminal` property

```python
@property
def is_terminal(self) -> bool:
    return self.status in (SagaStatus.COMPLETED, SagaStatus.FAILED, SagaStatus.COMPENSATED)
```

Terminal sagas ignore all incoming events. The `SagaManager` skips them entirely.

## Custom state subclasses

Subclass `SagaState` to add domain-specific fields:

```python
class OrderFulfillmentState(SagaState):
    order_total: Money = Money(amount=0, currency="EUR")
    shipping_address: str = ""

class OrderFulfillmentSaga(Saga[OrderFulfillmentState]):
    state_class = OrderFulfillmentState
    listens_to = [...]
```

## Next steps

- [Saga Lifecycle](saga-lifecycle.md) — how statuses transition
- [Saga Compensation](saga-compensation.md) — how the compensation stack works
- [How to Define a Saga](../../how-to/sagas/define-saga.md) — build a saga with custom state
