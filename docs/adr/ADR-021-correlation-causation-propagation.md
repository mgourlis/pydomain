# ADR-021: Correlation/Causation Propagation via UoW Stamping

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

In a distributed system, a single user request may trigger a chain of commands and events across multiple aggregates and services. Without correlation IDs, tracing the causal chain is impossible — you cannot determine which user request produced a given event.

The challenge: the aggregate that records events has no knowledge of the command that triggered it. The correlation chain must be injected at the infrastructure level, not the domain level.

## Decision

The `CommandBus` propagates tracing IDs through the `UnitOfWork`:

1. **On first command**: A new `correlation_id` is derived from `command.command_id` (or from explicit `correlation_id` on the command, set by the saga manager).

2. **Stamping the UoW**: The bus sets `_correlation_id` and `_causation_id` on the UoW via `setattr()`:

```python
correlation_id = command.correlation_id or command.command_id
causation_id = command.causation_id or command.command_id

setattr(uow, "_correlation_id", correlation_id)
setattr(uow, "_causation_id", causation_id)
```

3. **Event stamping**: During `UnitOfWork.commit()`, the `_collect_and_stamp()` hook calls `DomainEvent.stamp()` on each collected event, embedding the correlation/causation IDs:

```python
def _collect_and_stamp(self):
    for repo in self._repos.values():
        for event in repo.pull_events():
            stamped = event.stamp(
                correlation_id=self._correlation_id,
                causation_id=self._causation_id,
            )
            self._events.append(stamped)
```

4. **Saga propagation**: The saga manager sets `correlation_id` and `causation_id` on forwarded commands, maintaining the chain across saga steps.

`setattr()` is used because the `UnitOfWork` Protocol does not declare private attributes — at runtime the object is always an `AbstractUnitOfWork` that does.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Pass correlation IDs as handler parameters | Leaks infrastructure concern into handler signature; every handler must propagate manually |
| Thread-local context | Does not work with async (coroutines switch between tasks); not thread-safe |
| Context variable (`contextvars`) | Works with async but is implicit — harder to test; hidden coupling |
| No correlation tracking | Impossible to trace request chains across services; debugging nightmare |

## Consequences

### Positive

- Every domain event carries `correlation_id` and `causation_id` — full causal chain traceability.
- The aggregate remains unaware of tracing — domain purity is preserved.
- The saga manager can propagate correlation IDs across saga steps, enabling end-to-end tracing.
- Backward compatible: commands without explicit correlation IDs use `command_id` as both.

### Negative

- `setattr()` is used because the Protocol doesn't declare private attributes — slightly fragile but necessary for the abstraction.

### Neutral

- `MessageContext` also carries `correlation_id` and `causation_id` for pipeline behaviors that need tracing.

## References

- `src/pydomain/cqrs/command_bus.py` — `CommandBus.dispatch()` sets correlation/causation on UoW
- `src/pydomain/cqrs/unit_of_work.py` — `AbstractUnitOfWork._collect_and_stamp()` stamps events
- `src/pydomain/ddd/domain_event.py` — `DomainEvent.stamp()` creates new copy with tracing IDs
- ADR-011: DomainEvent `stamp()` Preserves Immutability
