# ADR-058: MessageBus Dispatch Extended for DomainEvent

## Status

Accepted

## Date

2026-05-22

## Context

`MessageBus.dispatch()` routes commands (with UoW lifecycle and pipeline behaviors) and
queries (read-only, no UoW). Domain events, however, were only dispatched as a side effect
of command handling — the `CommandBus` collected events after handler execution and returned
them, and `MessageBus` dispatched those collected events to registered handlers.

This created a gap: integration events arriving from external message brokers (via the
`InboundEventGateway`) need to be hydrated, translated to `DomainEvent` instances, and
dispatched to the same local event handlers. Without direct `DomainEvent` dispatch, these
externally-originated events would require a separate dispatch path or a synthetic command
wrapper.

The domain events arriving from external sources already represent committed state — they
should *not* pass through a UoW or pipeline behaviors.

## Decision

Extend `MessageBus.dispatch()` to accept `Command[Any] | Query[Any] | DomainEvent`:

```python
async def dispatch(self, message: Command[Any] | Query[Any] | DomainEvent) -> Any:
    if isinstance(message, DomainEvent):
        await self._dispatch_event(message)
        return None
    if isinstance(message, Command):
        result, events = await self._command_bus.dispatch(message)
        await self._dispatch_events(events)
        return result
    if isinstance(message, Query):
        return await self._query_bus.dispatch(message)
    raise TypeError(...)
```

**Routing rules:**
- **DomainEvent**: dispatched directly to registered event handlers. No UoW, no pipeline
  behaviors. Returns `None`.
- **Command**: unchanged — routed to `CommandBus` with UoW lifecycle, returns
  `CommandResult`.
- **Query**: unchanged — routed to `QueryBus`, returns `QueryResult`.

Domain event handlers registered for `DomainEvent` subtypes are invoked via the same
`_dispatch_event()` method that post-command event dispatch uses. Failure isolation
(per-handler try/except per ADR-046) applies equally.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Separate `publish_event()` method on MessageBus | Duplicates dispatch surface; callers must decide which method to call. A unified `dispatch()` is simpler. |
| Synthetic command wrapper for each external event | Commands require UoW and handlers. Forces plumbing for events that already represent committed state. |
| InboundEventGateway registers handlers directly with the bus internals | Breaks encapsulation; gateway would need access to `_dispatch_event()`. |

## Consequences

### Positive

- Unified dispatch entry point — callers always call `bus.dispatch()` regardless of message type.
- Externally-originated domain events reuse the same handler infrastructure as internally-generated ones.
- No UoW overhead for events that already represent committed state.

### Negative

- The `dispatch()` method now has three branches, increasing cyclomatic complexity.
- Callers cannot distinguish "this event was local" from "this event arrived from outside" at the handler level.

### Neutral

- The `TypeError` guard is extended to cover the new `DomainEvent` branch — the branch check itself acts as a security boundary, preventing accidental dispatch of irrelevant message types.

## References

- `src/pydomain/infrastructure/message_bus.py` — `dispatch()` method at line 160
- `tests/infrastructure/test_message_bus.py` — `TestDomainEventDispatch` class (8 test methods)
- `src/pydomain/infrastructure/message_subscriber.py` — `InboundEventGateway` (consumer)
- ADR-045: MessageBus as Level 3 facade
- ADR-046: Event handlers fail independently
