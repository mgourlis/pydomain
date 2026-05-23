# ADR-032: Saga Correlation via `event.correlation_id`

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

When a saga dispatches a forward command, the events produced by that command's handler must be traceable back to the original saga instance. Without correlation propagation, it is impossible to determine which saga triggered a given command or which events resulted from it.

This is especially critical for:
- **Debugging**: Tracing the causal chain from user request → saga → command → event → saga.
- **Recovery**: Identifying which pending commands belong to which saga instance.
- **Observability**: Distributed tracing across service boundaries.

## Decision

The `SagaManager` propagates tracing IDs onto forwarded commands via `_trace_command()`:

```python
@staticmethod
def _trace_command(cmd, state, causation_id=None):
    return cmd.model_copy(update={
        "correlation_id": state.correlation_id,
        "causation_id": causation_id or state.id,
    })
```

The propagation chain:
1. First event triggers saga creation → `state.correlation_id = event.correlation_id or event.event_id`.
2. Saga dispatches commands → manager stamps each command with `correlation_id` and `causation_id`.
3. `CommandBus` propagates these IDs to the UoW → events produced carry the same `correlation_id`.
4. When those events return to the saga, the correlation chain continues.

This creates an end-to-end tracing chain: **original event → saga → command → new events → saga**.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| No correlation propagation | Impossible to trace request chains; debugging is guesswork |
| Thread-local correlation context | Does not work with async (coroutines); not portable across services |
| Saga ID only (no causation) | Cannot distinguish between multiple commands from the same saga step |

## Consequences

### Positive

- Full causal chain traceability across saga steps.
- `correlation_id` links all events/commands in the same business process.
- `causation_id` identifies which specific event triggered each command.
- Compatible with distributed tracing systems (OpenTelemetry, etc.).

### Negative

- Commands are mutated via `model_copy` — a new instance is created for each dispatch (negligible cost).

### Neutral

- The saga state itself stores `correlation_id` and `causation_id` — persisted alongside the saga for recovery.

## References

- `src/pydomain/cqrs/saga/manager.py` — `SagaManager._trace_command()` method
- `src/pydomain/cqrs/saga/state.py` — `SagaState.correlation_id`, `SagaState.causation_id`
- ADR-021: Correlation/Causation Propagation via UoW Stamping
