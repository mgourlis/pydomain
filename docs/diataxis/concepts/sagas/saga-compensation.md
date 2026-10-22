# Saga Compensation

> **Adoption Level:** 5 — Sagas & Process Managers
> **Module:** `pydomain.cqrs.saga.saga`
> **Prerequisites:** [Saga](saga.md), [Saga State](saga-state.md)

## What is compensation?

Compensation is the mechanism that undoes completed steps when a saga fails partway through. Unlike a database ROLLBACK, compensation executes separate *undo commands* for each step that already succeeded — in reverse order.

If a saga does Reserve → Charge → Ship and fails at Ship, compensation runs CancelShip → Refund → CancelReservation (LIFO).

## How compensation works in pydomain

Each forward step can register a compensating command:

```python
self.on(OrderCreated,
        send=lambda e: ReserveItems(order_id=e.order_id),
        compensate=lambda e: CancelReservation(order_id=e.order_id),
        compensate_description="Cancel the item reservation")
```

The compensating command is pushed onto `state.compensation_stack` as a `CompensationRecord`. The description is stored alongside it for audit traceability.

### CompensationRecord

```python
class CompensationRecord(BaseModel):
    command_type: str        # "CancelReservation"
    data: dict[str, Any]     # {"order_id": "..."}
    description: str         # "Cancel the item reservation"
    module_name: str         # "myapp.orders.commands"
```

## LIFO execution

When `saga.fail(reason, compensate=True)` is called (or the `fail=True` flag is set in `on()`), the saga:

1. Sets `status = COMPENSATING`
2. Sets aside any queued forward commands (they are not discarded)
3. Pops records from `compensation_stack` in LIFO order
4. Hydrates each into a live `Command` via `hydrate_command()`
5. Queues them for dispatch

The `SagaManager` then sends the forward commands that were set aside first, then dispatches each compensation command, through the dispatcher.

## Compensation lifecycle

```
┌──────────┐   fail()    ┌───────────────┐   dispatch all   ┌──────────────┐
│ RUNNING  │ ──────────→ │ COMPENSATING  │ ──────────────→ │ COMPENSATED  │
└──────────┘             └───────────────┘                  └──────────────┘
                               │                                    ↑
                               │ (some compensations fail)          │
                               └────────────────────────────────────┘
                               │                                    │
                               ▼                                    │
                          ┌──────────┐                              │
                          │  FAILED  │ (with failed_compensations)  │
                          └──────────┘                              │
```

If all compensation commands dispatch successfully → `COMPENSATED`. If any fail → `FAILED` with records in `state.failed_compensations`.

## Manual compensation

For imperative-style handlers, add compensations manually:

```python
async def handle_order_created(self, event: OrderCreated) -> None:
    self.dispatch(ReserveItems(order_id=event.order_id))
    self.add_compensation(
        CancelReservation(order_id=event.order_id),
        description="Cancel reservation for order"
    )
```

## Command hydration

Compensation records store serialized command data (`command_type`, `data`, `module_name`). On execution, `hydrate_command()` reconstructs live `Command` instances:

```python
from pydomain.cqrs.saga import hydrate_command

command = hydrate_command(
    module_name="myapp.orders.commands",
    command_type="CancelReservation",
    data={"order_id": UUID("...")},
)
```

If hydration fails (e.g., the command class was removed in a refactor), the failure is recorded in `state.failed_compensations` and logged as an error. The saga continues compensating remaining steps.

## Compensation and the compensation stack integrity

- The compensation stack is **cleared** on successful `complete()` — a completed saga has nothing to undo
- The stack is **preserved** during `SUSPENDED` — the saga may still need to compensate later
- `state.max_step_history` and `pruning_policy` settings do **not** affect the compensation stack; it's an independent collection

## Next steps

- [How to Implement Saga Compensation](../../how-to/sagas/saga-compensation.md) — step-by-step guide
- [Saga Error Handling](saga-error-handling.md) — failure modes and recovery strategies
- [Saga Lifecycle](saga-lifecycle.md) — the full state machine
