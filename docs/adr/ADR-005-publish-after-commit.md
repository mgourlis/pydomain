# ADR-005: Publish Events After Commit, Never Before

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Domain events are collected during command handling and need to be dispatched to event handlers, saga managers, and integration event publishers. The question is when to dispatch: immediately on `_add_event()`, after handler completion but before commit, or after commit.

## Decision

Events are only dispatched after `UnitOfWork.commit()` succeeds. The `CommandBus` returns collected events alongside the command result, and the `MessageBus` dispatches them afterward.

Individual handler failures do **not** affect other handlers — the `MessageBus._dispatch_events()` catches and logs per-handler exceptions.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Dispatch on `_add_event()` | Handler may publish integration event, then transaction rolls back — external world believes something happened that didn't |
| Dispatch after handler, before commit | Same consistency risk; handler-triggered side effects may not reflect committed state |

## Consequences

### Positive

- Consistency guarantee: event handlers never see rolled-back state.
- Eliminates an entire class of duplicate/inconsistent events.
- Clean separation: aggregate enforces invariants, UoW persists and publishes, handler orchestrates.
- Handler failure isolation: one handler's failure does not prevent others from processing.

### Negative

- If dispatch itself fails after commit, events are "lost" (the state changed but handlers weren't notified). Requires an outbox pattern or retry mechanism for full reliability.

## References

- §9.8 Publish-After-Commit
