# ADR-062: EventBus as First-Class Citizen in the Application Layer

## Status

Accepted

## Date

2026-06-02

## Context

The library has three message types in its CQRS model — Commands, Queries, and Domain Events — but the architectural treatment is asymmetric:

| Component | Location | Has its own class? |
|---|---|---|
| `CommandBus` | `src/pydomain/cqrs/` (application layer) | ✅ `command_bus.py` |
| `QueryBus` | `src/pydomain/cqrs/` (application layer) | ✅ `query_bus.py` |
| Event dispatch | `src/pydomain/infrastructure/message_bus.py` | ❌ Inline in `MessageBus` |

`MessageBus` (a Level 3 infrastructure facade, per ADR-045) delegates commands to `CommandBus` and queries to `QueryBus`, but handled event registration and dispatch itself via `_event_handlers`, `register_event()`, `_dispatch_event()`, and `_dispatch_events()` — all inline.

This created two problems:

1. **Inconsistency** — Event dispatch is conceptually an application-layer concern (route a message to a handler), yet it lived in infrastructure. Anyone wanting an event bus for standalone use (e.g., in tests, or without the full MessageBus stack) had no clean entry point.

2. **No event idempotency** — Unlike commands (which have `IdempotencyBehavior` via `ProcessedCommandStore`), events had no built-in mechanism to prevent duplicate handling when the same integration event arrives twice from a message broker (at-least-once delivery). The `InboundEventGateway` (ADR-060) set `causation_id = integration_event.event_id` as a tracing hint, but this was passive — no active dedup.

Separately, the `ProcessedCommandStore` protocol was named for commands only, making it unclear whether it could be reused for event idempotency.

## Decision

### 1. Extract `EventBus` into the CQRS application layer

A new `EventBus` class in `src/pydomain/cqrs/event_bus.py` mirrors `CommandBus` and `QueryBus`:

```python
class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[type[DomainEvent], list[MessagePipeline]] = {}

    def register[TEvent: DomainEvent](self, event_type, handler, behaviors=None) -> None: ...
    async def dispatch(self, event: DomainEvent) -> None: ...
    async def dispatch_many(self, events: list[DomainEvent]) -> None: ...
```

`MessageBus` gains an optional `event_bus: EventBus | None` constructor parameter (default: auto-create) and delegates all event operations to it:

- `register_event()` → `self._event_bus.register()`
- `dispatch(DomainEvent)` → `self._event_bus.dispatch()`
- `dispatch(Command)` → `CommandBus` + `self._event_bus.dispatch_many(events)`

### 2. Rename `ProcessedCommandStore` → `ProcessedMessageStore`

The protocol is broadened from command-only to cover both commands and events:

| Today | Tomorrow |
|---|---|
| `ProcessedCommandStore` | `ProcessedMessageStore` |
| `get(command_id)` | `get(message_id)` |
| `set(command_id, result)` | `set(message_id, result)` |
| `contains(command_id)` | `contains(message_id)` |
| — | **+ `check_and_set(message_id) -> bool`** (NEW) |

`check_and_set()` provides atomic check-and-mark semantics for use cases where no result caching is needed (events).

### 3. Add `EventIdempotencyBehavior` as a pipeline behavior

> **Amended by [ADR-080](ADR-080-identify-delivered-events-for-event-idempotency.md) points 1, 3 and 4.** Takes effect with DCE-131 (W2-2). The key is `uuid5(uuid5(<namespace>, consumer), str(event_id))`, not `causation_id`, where `consumer=` names the handler (optional in 0.4.0 with a fallback to the handler's `module:qualname` and a `DeprecationWarning`, required in 0.5.0). The event is marked after its handler succeeds: with a store that also implements `MessageReservationStore` the behavior reserves the key first, and a concurrent duplicate raises `EventInProgressError`; without one it checks `contains` and calls `check_and_set` after the handler.

Rather than baking idempotency into the `EventBus` itself, a new pipeline behavior is added to `src/pydomain/cqrs/behaviors.py`, following the same pattern as `IdempotencyBehavior` for commands:

```python
class EventIdempotencyBehavior:
    def __init__(self, store: ProcessedMessageStore) -> None: ...

    async def handle(self, ctx, next) -> Any:
        event = ctx.message
        if not isinstance(event, DomainEvent) or event.causation_id is None:
            return await next()
        if await self._store.check_and_set(event.causation_id):
            return None  # Already processed
        return await next()
```

Users opt in per event handler:

```python
bus = EventBus()
bus.register(OrderReceived, my_handler, behaviors=[EventIdempotencyBehavior(store)])
```

**Why a pipeline behavior and not bus-level dedup**: Events have N handlers, and idempotency requirements may differ per handler. A pipeline behavior lets individual handlers opt in. Bus-level dedup would be all-or-nothing — if one handler needed idempotency, ALL handlers would skip on duplicate.

### 4. `EventBus` remains pure — no store injection

The `EventBus` has no `ProcessedMessageStore` dependency. It is purely a dispatch mechanism. All cross-cutting concerns (logging, tracing, idempotency, validation) are pipeline behaviors registered per-handler — consistent with the existing architecture where `CommandBus` does not own idempotency either.

## Alternatives Considered

| Alternative | Rejection Reason |
|---|---|
| **Keep event dispatch inline in MessageBus** | Perpetuates the asymmetry. No clean entry point for event-only usage. |
| **Inject ProcessedMessageStore into EventBus** | Makes EventBus responsible for idempotency — inconsistent with CommandBus which delegates idempotency to `IdempotencyBehavior`. Would need a `dispatch()`/`dispatch_many()` split (one checks store, the other doesn't) to avoid false dedup of sibling events from the same command. |
| **Create separate `ProcessedEventStore` protocol** | Unnecessary protocol proliferation. Commands and events both need "has this ID been processed?" semantics. A single `ProcessedMessageStore` covers both use cases. |
| **Keep `ProcessedCommandStore` name and add a separate event store** | Confusing naming — developers would need to learn two protocols for the same concept. The rename signals the unified intent. |

## Consequences

### Positive

- **Architectural consistency** — `EventBus` is now a peer of `CommandBus` and `QueryBus` in the application layer. All three buses follow the same pattern: register handler → dispatch message.
- **Event-only usage** — Tests and simple scenarios can use `EventBus` directly without the full `MessageBus` facade.
- **Opt-in idempotency** — Event handlers choose whether to be idempotent via `EventIdempotencyBehavior`, consistent with how commands opt in via `IdempotencyBehavior`.
- **Unified store protocol** — `ProcessedMessageStore` serves both commands and events, reducing protocol surface area.
- **No false dedup** — Since idempotency is per-handler via pipeline behaviors, sibling events from the same command execution are never incorrectly skipped.

> **Note from [ADR-080](ADR-080-identify-delivered-events-for-event-idempotency.md) point 9.** Takes effect with DCE-131 (W2-2). Under the `causation_id` key, every event of one command shared one key and only the first reached an idempotent handler (CQRS-05). From ADR-080 each event and consumer has its own key, which makes this statement hold.

### Negative

- **Public API surface grows** — `EventBus` and `EventIdempotencyBehavior` are new exports users need to learn.
- **Migration** — Users accessing `bus._event_handlers` (private, but possible) break. Logger namespace for event errors changes from `pydomain.message_bus` to `pydomain.event_bus`.

### Neutral

- **MessageBus constructor** gains an optional `event_bus` parameter (backward compatible — defaults to auto-create).
- **`FakeProcessedCommandStore`** is renamed to `FakeProcessedMessageStore` and gains a `check_and_set()` method.
- **`InboundEventGateway`** is unaffected — it still calls `MessageBus.dispatch(domain_event)`.

## References

- `src/pydomain/cqrs/event_bus.py` — The new `EventBus` class
- `src/pydomain/cqrs/behaviors.py` — `EventIdempotencyBehavior` class
- `src/pydomain/cqrs/idempotency.py` — `ProcessedMessageStore` protocol
- `src/pydomain/infrastructure/message_bus.py` — Updated `MessageBus` (delegates to EventBus)
- `src/pydomain/testing/fake_processed_message_store.py` — Renamed fake
- ADR-017: Onion-style pipeline behaviors (the pattern `EventIdempotencyBehavior` follows)
- ADR-045: MessageBus as Level 3 facade (the architecture this evolves)
- ADR-046: Event handlers fail independently
- ADR-058: MessageBus dispatch extended for DomainEvent
- ADR-060: InboundEventGateway (sets causation_id for dedup)
