# ADR-045: MessageBus as Level 3 Facade — CommandBus + QueryBus + Event Dispatcher

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

The application needs a single entry point for dispatching commands and queries. Callers (route handlers, tests, CLI tools) should not need to know which bus to use — they just call `dispatch(message)`.

Separately, domain events collected after command execution must be dispatched to registered event handlers. This event dispatch is conceptually different from command/query dispatch (fire-and-forget, multiple handlers, failure isolation).

## Decision

`MessageBus` is a Level 3 facade that composes three subsystems:

```python
class MessageBus:
    def __init__(self, command_bus=None, query_bus=None):
        self._command_bus = command_bus or CommandBus()
        self._query_bus = query_bus or QueryBus()
        self._event_handlers: dict[type[DomainEvent], list[MessagePipeline]] = {}
```

**Unified dispatch**:

```python
async def dispatch(self, message: Command | Query) -> Any:
    if isinstance(message, Command):
        result, events = await self._command_bus.dispatch(message)
        await self._dispatch_events(events)  # Publish collected domain events
        return result
    if isinstance(message, Query):
        return await self._query_bus.dispatch(message)
    raise TypeError(...)
```

**Registration methods**:
- `register_command()` → delegates to `CommandBus.register()`
- `register_query()` → delegates to `QueryBus.register()`
- `register_event()` → stores handler in `_event_handlers` dict (multiple handlers per event type)

**Level breakdown**:
| Level | Component | Responsibility |
|-------|-----------|---------------|
| 1 | `CommandBus` / `QueryBus` | Route message to handler, manage UoW lifecycle |
| 2 | `MessagePipeline` | Compose pipeline behaviors around handler |
| 3 | `MessageBus` | Facade: route command/query + dispatch collected events |

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Separate `dispatch_command()` and `dispatch_query()` methods | Caller must know which method to call; no polymorphism |
| No facade — callers use CommandBus and QueryBus directly | Event dispatch must be wired manually; error-prone |
| Event dispatch in CommandBus | CommandBus would depend on event handler registration — scope creep |

## Consequences

### Positive

- Single `dispatch()` entry point — callers don't need to know the bus type.
- Event dispatch is automatic after command execution — no manual wiring.
- Clean composition: `MessageBus` delegates to `CommandBus` and `QueryBus`.
- Event handlers are managed separately from command/query handlers.

### Negative

- `isinstance` check on every dispatch — negligible cost, but not type-safe at compile time.

### Neutral

- The facade pattern is standard in CQRS frameworks (MediatR, Wolverine, etc.).

## References

- `src/pydomain/infrastructure/message_bus.py` — `MessageBus` class
- `src/pydomain/cqrs/command_bus.py` — `CommandBus`
- `src/pydomain/cqrs/query_bus.py` — `QueryBus`
- `src/pydomain/cqrs/behaviors.py` — `MessagePipeline`
