# ADR-039: Convention Dispatch in Projections — `_when_{TypeName}` Methods

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Projections must handle multiple event types. Without a dispatch mechanism, each projection would need a large `if isinstance(event, A) ... elif isinstance(event, B)` chain in its handler method.

This pattern is verbose, error-prone (forgetting an event type), and couples the projection's dispatch logic to its event handling.

## Decision

`EventSourcedProjection` uses **convention-based dispatch**: each event type maps to a method named `_when_{TypeName}`:

```python
class EventSourcedProjection(ABC):
    async def handle(self, event: DomainEvent) -> None:
        handler_name = f"_when_{type(event).__name__}"
        handler = getattr(self, handler_name, None)
        if handler is not None:
            await handler(event)
```

Subclasses define handlers by naming convention:

```python
class OrderSummaryProjection(EventSourcedProjection):
    name: ClassVar[str] = "order_summary"
    version: ClassVar[int] = 1

    async def _when_OrderPlaced(self, event: OrderPlaced) -> None:
        # Handle OrderPlaced
        ...

    async def _when_OrderShipped(self, event: OrderShipped) -> None:
        # Handle OrderShipped
        ...
```

If no `_when_{TypeName}` method exists for an event, it is **silently ignored** — projections only need to handle the events they care about.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| `isinstance` chain in `handle()` | Verbose; every projection repeats the dispatch pattern; easy to forget an event type |
| Decorator-based registration (`@handles(OrderPlaced)`) | Requires import-time side effects; harder to test; decorator ordering issues |
| Registry-based dispatch (dict mapping) | Must manually maintain mapping; easy to get out of sync with handler methods |

## Consequences

### Positive

- Convention eliminates boilerplate — one method per event type, named by convention.
- Adding a new event handler requires only adding a new method (open/closed principle).
- Unhandled events are silently ignored — projections don't need to handle every event.
- IDE auto-completion discovers `_when_*` methods easily.

### Negative

- Convention is not enforced by the type system — a typo in the method name creates a silent no-op.
- `getattr()` is slower than direct method calls (negligible for typical event processing).

### Neutral

- Same `isinstance` dispatch pattern as aggregates (ADR-012), but implemented via naming convention instead of explicit checks.

## References

- `src/pydomain/es/projection.py` — `EventSourcedProjection.handle()` dispatch logic
- ADR-012: isinstance Dispatch in Aggregates
