# ADR-031: SagaManager as Separate Orchestrator (Not in Saga Class)

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

The `Saga` class defines *what* a long-running process does: which events it handles, what commands it dispatches, and how it compensates on failure. But the lifecycle — loading state, persisting, dispatching commands through the bus, recovering from crashes — is infrastructure orchestration.

Mixing orchestration into the `Saga` class would:
1. Force the saga to depend on `CommandBus` and `SagaRepository` — infrastructure concerns leaking into domain.
2. Make testing harder — every test would need to mock the bus and repository.
3. Violate SRP — the saga would both define the process and manage its execution.

## Decision

Two separate classes with distinct responsibilities:

- **`Saga`**: Declarative process definition. Declares event handlers, command mappings, compensations, lifecycle transitions. No infrastructure dependencies. Testable in isolation.

- **`SagaManager`**: Orchestrator. Loads/creates state from repository, instantiates sagas, calls `handle(event)`, saves state, dispatches commands through `CommandBus`, manages recovery.

```python
class SagaManager:
    def __init__(self, repository, registry, command_bus):
        self.repository = repository
        self.registry = registry
        self.command_bus = command_bus

    async def handle(self, event: DomainEvent):
        # 1. Find saga classes for event type
        # 2. Load or create saga state
        # 3. Instantiate saga, call handle(event)
        # 4. Save state
        # 5. Dispatch pending commands via CommandBus
```

The saga manager can auto-register itself as an event handler via `bind_to(event_dispatcher)`.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Saga class owns orchestration | Infrastructure leaks into domain; harder to test; violates SRP |
| Static orchestration functions | Cannot inject different command buses or repositories for testing |
| Event-driven orchestration (saga reacts to its own events) | Infinite loop risk; harder to reason about; opaque control flow |

## Consequences

### Positive

- Clean separation of concerns: saga defines, manager orchestrates.
- Saga is testable without infrastructure — instantiate with state, call `handle(event)`, assert state changes.
- Manager is replaceable — different managers for different deployment scenarios.
- `bind_to()` makes integration with `MessageBus` trivial.

### Negative

- Two classes to understand instead of one.
- Manager is a god object for saga lifecycle — grows with each new orchestration concern.

### Neutral

- The manager can be extended with recovery, timeout, and retry logic without touching the saga class.

## References

- `src/pydomain/cqrs/saga/saga.py` — `Saga` class (declarative)
- `src/pydomain/cqrs/saga/manager.py` — `SagaManager` class (orchestration)
