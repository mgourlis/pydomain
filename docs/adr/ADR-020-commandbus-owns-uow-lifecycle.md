# ADR-020: CommandBus Owns UoW Lifecycle — Handlers Never Call Commit/Rollback

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

In a typical CQRS setup, the command handler loads aggregates, performs business logic, and persists changes. The question is: who manages the transaction boundary (commit/rollback)?

If the handler calls `commit()`, several problems arise:
1. The handler can forget to commit (silent data loss).
2. The handler can commit too early (before events are stamped).
3. The handler can catch and swallow errors, preventing rollback.
4. Event collection and stamping must happen at a specific point in the lifecycle — after handler completion but before commit.

## Decision

The **`CommandBus`** (not the handler) creates, commits, and rolls back the `UnitOfWork`:

```python
class CommandBus:
    async def dispatch(self, command):
        uow = entry.uow_factory()  # Bus creates the UoW

        async with uow:
            try:
                result = await entry.pipeline.execute(ctx, command)
                await uow.commit()          # Bus commits

                raw_events = uow.collect_events()
                return result, raw_events

            except Exception as exc:
                await uow.rollback()         # Bus rolls back
                raise CommandExecutionError(command) from exc
```

The handler receives the UoW as a parameter but never calls `commit()` or `rollback()`:

```python
async def handle(cmd: PlaceOrder, uow: UnitOfWork) -> PlaceOrderResult:
    order = await uow.orders.get_by_id(cmd.order_id)
    order.submit()
    await uow.orders.save(order)
    return PlaceOrderResult(order_id=order.id)
    # No commit — the bus handles it
```

The bus returns `(result, events)` — the caller (typically `MessageBus`) is responsible for dispatching the collected domain events.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Handler calls `uow.commit()` | Forgetting to commit = silent data loss; handler controls transaction boundary = inconsistent lifecycle |
| Handler calls `uow.commit()` in `finally` | Always commits even on error; cannot distinguish success from failure |
| Decorator-based commit | Non-obvious; hides transaction boundary; harder to debug |
| UoW auto-commits on `__aexit__` | Commits on exception exit unless explicitly checked; fragile |

## Consequences

### Positive

- Guaranteed commit/rollback lifecycle — the bus always commits on success and rolls back on failure.
- Event stamping happens at the right time (during `commit()` → `_collect_and_stamp()`).
- Handlers are simpler — pure business logic, no transaction management.
- `CommandExecutionError` wraps handler failures with the command context for debugging.

### Negative

- Handlers cannot control the transaction boundary (intentional — this is the trade-off).

### Neutral

- The `async with uow` context manager provides rollback-on-exit as a safety net if the bus crashes before explicit rollback.

## References

- `src/pydomain/cqrs/command_bus.py` — `CommandBus.dispatch()` creates UoW, commits, rolls back
- `src/pydomain/cqrs/handlers.py` — `CommandHandler` receives `uow` but never calls `commit()`/`rollback()`
- `src/pydomain/cqrs/unit_of_work.py` — `AbstractUnitOfWork.commit()` and `rollback()`
- ADR-005: Publish Events After Commit, Never Before
- ADR-016: Handler Signature Asymmetry
