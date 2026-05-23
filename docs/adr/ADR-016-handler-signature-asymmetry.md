# ADR-016: Handler Signature Asymmetry — CommandHandler gets UoW, others don't

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

In CQRS, commands and queries have fundamentally different responsibilities:

- **Commands** mutate state. They need transactional scope (Unit of Work) to access repositories, load aggregates, and persist changes atomically.
- **Queries** are read-only. They fetch data from a read store without modifying any state. No transactional scope needed.
- **Event handlers** react to domain events. They are fire-and-forget side effects (notifications, projections, orchestrations). No UoW — event handlers fail independently.

Giving all three handler types the same signature would misrepresent their responsibilities and encourage misuse (e.g., a query handler that mutates state via the UoW).

## Decision

Three distinct handler signatures reflecting CQRS separation:

```python
@runtime_checkable
class CommandHandler[TCommand, TResult](Protocol):
    async def __call__(self, command: TCommand, uow: UnitOfWork) -> TResult: ...

@runtime_checkable
class QueryHandler[TQuery, TResult](Protocol):
    async def __call__(self, query: TQuery) -> TResult: ...

@runtime_checkable
class EventHandler[TEvent](Protocol):
    async def __call__(self, event: TEvent) -> None: ...
```

- **CommandHandler**: Receives command AND `UnitOfWork`. The handler loads/persists aggregates through `uow` attributes. The handler must **not** call `uow.commit()` or `uow.rollback()` — the `CommandBus` manages the lifecycle (ADR-020).
- **QueryHandler**: Receives query only. Returns a typed result. No side effects.
- **EventHandler**: Receives event only. Returns `None` (fire-and-forget). Multiple handlers per event type. Handlers fail independently.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| All handlers receive UoW | Queries don't need transactions; event handlers must fail independently without transactional scope |
| All handlers receive no UoW | Command handlers need repository access; would require manual UoW injection, defeating the bus-managed lifecycle |
| UoW as optional parameter | Ambiguous — handler doesn't know if UoW is available; leads to `None` checks on every call |

## Consequences

### Positive

- Clear CQRS separation enforced by the type system — a query handler **cannot** receive a UoW.
- Event handlers are naturally independent — no transactional coupling between handlers.
- The CommandBus can safely manage UoW lifecycle (create, commit, rollback) because only command handlers have transactional scope.
- Signature tells you the handler's role: takes UoW = mutates state, returns None = side effect, no UoW = read-only.

### Negative

- Three different handler protocols to learn.
- Event handlers that need to dispatch commands must inject the `MessageBus` via their constructor (not via parameters).

### Neutral

- This asymmetry directly reflects CQRS principles: commands write, queries read, events react.

## References

- `src/pydomain/cqrs/handlers.py` — `CommandHandler`, `QueryHandler`, `EventHandler` Protocols
- `src/pydomain/cqrs/command_bus.py` — `CommandBus.dispatch()` passes UoW to handler
- `src/pydomain/cqrs/query_bus.py` — `QueryBus.dispatch()` does not pass UoW
- ADR-020: CommandBus Owns UoW Lifecycle
