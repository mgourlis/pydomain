# ADR-033: LIFO Compensation Stack via Serialized `CompensationRecord`

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

When a saga step fails, all previously completed steps must be undone in **reverse order**. If step 3 fails after steps 1 and 2 succeeded, compensation must undo step 2 first, then step 1. This is the Last-In-First-Out (LIFO) compensation pattern.

Compensating actions are commands — they must be persisted (for crash recovery), serializable (for storage), and typed (for dispatch through the command bus).

## Decision

Compensations are pushed onto a stack (`compensation_stack`) and executed in LIFO order:

```python
class CompensationRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    command_type: str
    data: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    module_name: str = ""
```

```python
class SagaState(AggregateRoot[UUID]):
    compensation_stack: list[CompensationRecord] = Field(default_factory=list)
```

- **Push**: `add_compensation(command, description)` creates a `CompensationRecord` and appends to the stack.
- **Pop**: `execute_compensations()` pops from the end of the stack (LIFO), hydrates each record back into a live `Command`, and dispatches it.

```python
async def execute_compensations(self):
    self.state.status = SagaStatus.COMPENSATING
    while self.state.compensation_stack:
        record = self.state.compensation_stack.pop()  # LIFO
        command = hydrate_command(record.module_name, record.command_type, record.data)
        if command:
            self.dispatch(command)
```

Each `CompensationRecord` is:
- **Frozen** (immutable Pydantic model) — compensations cannot be modified after registration.
- **Serializable** (`model_dump()` produces a dict) — persisted as part of `SagaState`.
- **Self-describing**: carries `module_name` and `command_type` for hydration.

Failed compensations are recorded in `failed_compensations` for audit and manual intervention.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| FIFO compensation (forward order) | Violates rollback semantics — step 3's compensation may depend on step 2 still being active |
| Compensation as event handlers | No guaranteed ordering; event handlers fail independently; cannot ensure all compensations run |
| Compensation as stored procedures | Database-coupled; not portable; hard to test |
| List of commands (not records) | Commands are frozen Pydantic models with extra="forbid" — cannot strip unknown fields during hydration |

## Consequences

### Positive

- Correct rollback semantics: LIFO ensures dependent compensations run in the right order.
- Crash-safe: `CompensationRecord` is serialized and persisted as part of `SagaState`.
- Failed compensations are audited, not silently lost.
- Compensation stack is cleared on saga completion — no stale compensations.

### Negative

- Compensation stack grows with forward steps — long-lived sagas may accumulate many records.
- Hydration failure (missing module/class) is logged but does not halt remaining compensations.

### Neutral

- `hydrate_command()` strips unknown keys before `model_validate()` to handle schema evolution (commands use `extra="forbid"` per ADR-014).

## References

- `src/pydomain/cqrs/saga/state.py` — `CompensationRecord`, `SagaState.compensation_stack`
- `src/pydomain/cqrs/saga/saga.py` — `add_compensation()`, `execute_compensations()`
- `src/pydomain/cqrs/saga/hydration.py` — `hydrate_command()` for reconstructing commands from records
- ADR-014: `frozen=True` and `extra="forbid"` on Commands and Queries
