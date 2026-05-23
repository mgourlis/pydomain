# ADR-017: Onion-style Pipeline Behaviors

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Cross-cutting concerns (logging, validation, locking, idempotency) need to wrap command and query dispatch without modifying the handler or the bus. MediatR-style pipeline behaviors provide a composable middleware layer.

The key design question is composition order: behaviors must run in a predictable sequence (e.g., logging → validation → idempotency → locking → handler) and each behavior must be able to short-circuit, modify input, or transform output.

## Decision

Pipeline behaviors use an **onion (decorator) model**. Each behavior receives a `next` callable and wraps it:

```python
@runtime_checkable
class PipelineBehavior(Protocol):
    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any: ...
```

`MessagePipeline` composes behaviors at registration time:

```python
class MessagePipeline:
    def __init__(self, handler, behaviors=None):
        self._handler = handler
        self._behaviors = behaviors or []

    async def execute(self, ctx, message):
        # Build onion: outermost behavior ... handler
        async def terminal():
            if ctx.kind == MessageKind.COMMAND:
                return await self._handler(message, ctx.uow)
            return await self._handler(message)

        chain = terminal
        for behavior in reversed(self._behaviors):
            prev = chain
            chain = self._wrap(behavior, prev, ctx)
        return await chain()
```

**Built-in behaviors** (in typical registration order):

| Slot | Behavior | Purpose |
|------|----------|---------|
| 1 | `LoggingBehavior` | Logs entry/success/failure with timing |
| 2 | `ValidationBehavior` | Runs registered validators before handler |
| 3 | `IdempotencyBehavior` | Returns cached result for duplicate commands |
| 4 | `AggregateLockingBehavior` | Acquires sorted locks to prevent deadlocks |

Behaviors are registered per-command-type with the handler:

```python
bus.register(
    PlaceOrder, handler, uow_factory,
    behaviors=[LoggingBehavior(), ValidationBehavior()]
)
```

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Before/after hooks on the bus | Non-composable; fixed order; cannot short-circuit |
| Decorator chain on handler | Each decorator wraps the previous; hard to manage registration order; no shared context |
| Event-driven middleware | Too complex for synchronous pipeline; no guaranteed order |
| Global middleware list | All commands get all behaviors; no per-command customization |

## Consequences

### Positive

- Composable — each behavior is independent and testable in isolation.
- Onion model is familiar from MediatR and ASP.NET middleware.
- Per-command-type registration allows targeted behaviors (not all commands need locking).
- `MessageContext` carries shared state through the pipeline (correlation IDs, metadata).
- Behaviors can short-circuit (validation fails → handler never called).

### Negative

- Behavior order matters and must be documented.
- The onion model creates closures — slight overhead (negligible for typical 1-4 behaviors).

### Neutral

- `MessagePipeline` is constructed once at registration time and reused across dispatches.

## References

- `src/pydomain/cqrs/behaviors.py` — `PipelineBehavior` Protocol, `MessagePipeline`, `LoggingBehavior`, `ValidationBehavior`, `IdempotencyBehavior`, `AggregateLockingBehavior`
- `src/pydomain/cqrs/command_bus.py` — `CommandBus.register()` accepts `behaviors` parameter
- `src/pydomain/cqrs/query_bus.py` — `QueryBus.register()` accepts `behaviors` parameter
