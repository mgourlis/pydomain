# ADR-047: `bootstrap()` as Composition Root

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

The library needs a single function that wires together all infrastructure dependencies (event store, message bus, repositories, handlers, snapshot store) into a configured application object. Tests call it with fakes; production calls it with real adapters. The handlers don't change.

Without a composition root, each test and each production entry point must manually wire dependencies — duplicated, error-prone, and fragile.

## Decision

`bootstrap()` is the composition root. It creates an `Application` object with all dependencies wired:

```python
async def bootstrap(
    event_store: EventStore | None = None,
    snapshot_store: SnapshotStore | None = None,
    message_bus: MessageBus | None = None,
    message_broker: MessageBroker | None = None,
    event_registry: EventRegistry | None = None,
) -> Application:
    bus = message_bus or MessageBus()
    registry = event_registry or EventRegistry()

    if message_broker is not None:
        await message_broker.start()

    return Application(
        message_bus=bus,
        event_registry=registry,
        snapshot_store=snapshot_store,
    )
```

`Application` wraps the configured `MessageBus`:

```python
class Application:
    async def dispatch(self, message: Command | Query) -> Any:
        return await self._message_bus.dispatch(message)
```

**Key properties**:
- **All parameters optional**: Defaults create fresh instances — tests can selectively inject fakes.
- **Handler registration is separate**: `bootstrap()` creates the bus; handlers are registered after bootstrap via `bus.register_command()`, etc.
- **Message broker lifecycle**: `bootstrap()` calls `message_broker.start()` if provided.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Manual wiring in each test/entry point | Duplicated; error-prone; tests must know wiring details |
| Auto-discovery via import scanning | Fragile; implicit; hard to debug; side effects at import time |
| DI container (dependency-injector, etc.) | Over-engineered for a library; adds a framework dependency |

## Consequences

### Positive

- Single function wires the entire application — no scattered dependency construction.
- Tests inject fakes selectively — only override what they need.
- `Application.dispatch()` is the single entry point for all message handling.
- Production and test wiring differ only in the adapters passed to `bootstrap()`.

### Negative

- Handler registration happens after bootstrap — two-step setup (bootstrap then register).

### Neutral

- The pattern is borrowed from Harry Percival's "Architecture Patterns with Python" bootstrap pattern.

## References

- `src/pydomain/infrastructure/bootstrap.py` — `bootstrap()`, `Application`
- `src/pydomain/infrastructure/message_bus.py` — `MessageBus`
- `src/pydomain/infrastructure/event_registry.py` — `EventRegistry`
