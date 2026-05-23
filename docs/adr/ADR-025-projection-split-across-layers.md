# ADR-025: Projection Split Across Layers — CQRS Protocol, ES Implementation

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

ADR-024 establishes that `Projection` (CQRS) and `EventSourcedProjection` (ES) are two separate types. The split exists because:

1. **CQRS projection** is a pure contract: "apply events and rebuild state." It knows nothing about event streams, checkpoints, or subscription runners.
2. **ES projection** adds event-sourcing concerns: checkpoint tracking, convention-based dispatch, and stream position management.

The dependency graph is strict: `cqrs` must not import from `es`. So the CQRS layer defines its own `Projection` Protocol, and the ES layer provides a concrete ABC for event-sourced projections.

## Decision

Three related abstractions in two layers:

### CQRS Layer (`cqrs/projection.py`)

```python
@runtime_checkable
class Projection[StateT](Protocol):
    async def apply(self, event: DomainEvent) -> None: ...
    async def rebuild(self, events: Sequence[DomainEvent]) -> None: ...

@runtime_checkable
class ProjectionStore(Protocol):
    async def load(self, projection_id: str) -> Any | None: ...
    async def save(self, projection_id: str, state: Any) -> None: ...
```

- `Projection[StateT]`: Protocol for applying events and rebuilding read models. Pure CQRS concern.
- `ProjectionStore`: Persists read model state (the derived state, not the event stream position).

### ES Layer (`es/projection.py`)

```python
class EventSourcedProjection(ABC):
    _checkpoint: int  # Tracks stream position

    async def handle(self, event: DomainEvent) -> None:
        handler_name = f"_when_{type(event).__name__}"
        handler = getattr(self, handler_name, None)
        if handler:
            await handler(event)
        self._checkpoint += 1

    def reset(self) -> None:
        self._checkpoint = 0
```

- `EventSourcedProjection`: ABC with convention dispatch and checkpoint tracking. ES concern.
- `CheckpointStore` (`es/checkpoint_store.py`): Tracks stream position (a single integer per projection).

### Key Distinctions

| Concern | CQRS (`Projection`) | ES (`EventSourcedProjection`) |
|---------|---------------------|------------------------------|
| Contract type | Protocol | ABC |
| State type | Generic `StateT` | Internal |
| Dispatch | User-defined | Convention (`_when_{TypeName}`) |
| Position tracking | None | `_checkpoint` |
| Persistence | `ProjectionStore` | `CheckpointStore` |

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Single `Projection` base class with checkpoint support | Layer violation: CQRS must not depend on ES |
| `EventSourcedProjection` extends `Projection` | Same layer violation — ES would depend on CQRS definition |
| No CQRS `Projection` Protocol | Query handlers have no contract to implement; read model rebuilding is ad-hoc |

## Consequences

### Positive

- Clean layer separation: `cqrs` has no `es` imports.
- CQRS projection is a Protocol — any class with `apply()` and `rebuild()` conforms.
- ES projection provides concrete infrastructure (convention dispatch, checkpoints).
- `ProjectionStore` (read model state) and `CheckpointStore` (stream position) have distinct responsibilities.

### Negative

- Two projection abstractions may confuse newcomers (addressed in ADR-024).
- Users building event-sourced projections must understand both layers.

### Neutral

- A class can satisfy both `Projection` and `EventSourcedProjection` simultaneously.

## References

- `src/pydomain/cqrs/projection.py` — `Projection[StateT]` Protocol, `ProjectionStore`
- `src/pydomain/es/projection.py` — `EventSourcedProjection` ABC
- `src/pydomain/es/checkpoint_store.py` — `CheckpointStore` Protocol
- ADR-024: Two Separate Projection Types by Naming Convention
