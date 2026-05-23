# ADR-046: Event Handlers Fail Independently — Per-Handler Try/Except

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Domain events may have multiple handlers: one sends an email, one updates a projection, one triggers a saga. If the email handler fails (SMTP down), the projection and saga should still run. Event handlers are side effects — one handler's failure must not affect others.

This is a core CQRS principle: events are facts. The fact that `OrderPlaced` happened is independent of whether the notification was sent.

## Decision

`MessageBus._dispatch_event()` wraps each handler in its own try/except:

```python
async def _dispatch_event(self, event: DomainEvent) -> None:
    pipelines = self._event_handlers.get(type(event), [])
    for pipeline in pipelines:
        try:
            await pipeline.execute(ctx, event)
        except Exception:
            logger.exception(
                "Event handler %s failed for %s",
                ..., type(event).__name__,
            )
            # Exception is caught and logged; remaining handlers continue
```

Key behaviors:
- **Per-handler isolation**: Each handler runs in its own try/except block.
- **No propagation**: Handler exceptions are caught, logged, and swallowed.
- **Sequential execution**: Handlers run one at a time in registration order.
- **No UoW**: Event handlers manage their own persistence — no transactional scope from the bus.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Abort on first handler failure | One failing handler prevents all subsequent handlers; cascading failure |
| Transactional event handlers (all-or-nothing) | Defeats the purpose of independent handlers; one bad handler rolls back everything |
| Retry with backoff per handler | Adds complexity; may mask persistent failures; not the bus's responsibility |

## Consequences

### Positive

- One handler's failure does not affect other handlers — true independence.
- Errors are logged with full traceback for debugging.
- System remains operational even when individual handlers fail.
- Simple implementation — standard try/except per handler.

### Negative

- Failed handlers are silently swallowed (logged but not re-raised) — operators must monitor logs.
- No built-in retry mechanism — failed handlers must implement their own retry logic.

### Neutral

- This matches the "events fail independently" principle from the DDD wiki and the project's copilot-instructions.md.

## References

- `src/pydomain/infrastructure/message_bus.py` — `MessageBus._dispatch_event()` method
- `.github/copilot-instructions.md` — "Events fail independently" principle
