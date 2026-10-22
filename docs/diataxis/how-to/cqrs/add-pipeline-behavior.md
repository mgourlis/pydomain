# How to Add a Pipeline Behavior

> **Prerequisites:** [Pipeline Behaviors concept](../../concepts/cqrs/pipeline-behaviors.md), [Configure the Command Bus](configure-command-bus.md)

## Problem

You need to add cross-cutting middleware — logging, validation, metrics, retry — to your command or query handlers without modifying handler code.

## Solution

Create a class implementing the `PipelineBehavior` protocol and pass it to the bus at registration time.

## Steps

### 1. Implement the behavior

```python
from pydomain.cqrs.behaviors import PipelineBehavior, MessageContext, NextHandler


class MetricsBehavior:
    def __init__(self, metrics: MetricsCollector) -> None:
        self._metrics = metrics

    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        name = type(ctx.message).__name__
        self._metrics.increment(f"cqrs.{name}.received")

        try:
            result = await next()
            self._metrics.increment(f"cqrs.{name}.success")
            return result
        except Exception:
            self._metrics.increment(f"cqrs.{name}.failure")
            raise
```

The pattern: do something before `await next()`, then do something after.

In a command pipeline, `next()` includes the commit: the bus runs your handler and then commits the unit of work inside the innermost step. So, after `await next()`:

- it returned: the command committed and `ctx.new_events` holds the committed events, or an inner behavior short-circuited, for example with a cached result, and nothing was committed;
- it raised: usually the handler or the commit failed, and the rollback runs after your behavior returns; the `except` branch above counts a failed commit as a failure. [What `next()` means in a command pipeline](../../concepts/cqrs/pipeline-behaviors.md#what-next-means-in-a-command-pipeline) lists every case.

Don't write through `ctx.uow` after `next()`: that work is not committed. Do it in the handler, or before `next()`. Call `next()` at most once in a command pipeline: a second call raises `PipelineReentryError` before the handler runs, and the dispatch fails with `CommandExecutionError` (its cause is the `PipelineReentryError`). Call it at most once and never concurrently: with `asyncio.gather(next(), next())` the second call is refused, but `gather` does not cancel the first run, so it outlives the failed dispatch and commits after the dispatch was rolled back. To retry, dispatch the command again. Query and event pipelines allow any number of calls.

### 2. Add it to the bus

```python
bus.register(
    command_type=PlaceOrder,
    handler=PlaceOrderHandler(pricing_service),
    uow_factory=create_order_uow,
    behaviors=[
        LoggingBehavior(),
        MetricsBehavior(metrics_collector),  # Your custom behavior
        ValidationBehavior(),
    ],
)
```

### 3. Verify behavior ordering

Behaviors run in registration order — outermost first:

```
LoggingBehavior (outermost)
  └── MetricsBehavior
        └── ValidationBehavior
              └── Handler, then the commit (commands)
        └── MetricsBehavior (after)
  └── LoggingBehavior (after)
```

## Using MessageContext

Behaviors can read and write to `ctx.metadata` to pass data downstream:

```python
class RequestIdBehavior:
    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        request_id = str(uuid7())
        ctx.metadata["request_id"] = request_id

        logger.info("Request %s: %s", request_id, type(ctx.message).__name__)
        result = await next()
        logger.info("Request %s: completed", request_id)
        return result
```

## Branching on MessageKind

Behaviors can branch on `ctx.kind` for different logic per message type:

```python
class ThrottlingBehavior:
    def __init__(self, rate_limiter: RateLimiter) -> None:
        self._limiter = rate_limiter

    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        if ctx.kind == MessageKind.COMMAND:
            key = f"cmd:{type(ctx.message).__name__}"
        elif ctx.kind == MessageKind.QUERY:
            key = f"qry:{type(ctx.message).__name__}"
        else:
            key = "event"

        await self._limiter.acquire(key)
        return await next()
```

## Built-in Behaviors Quick Reference

| Behavior | Slot | Purpose | Required Dependencies |
|----------|------|---------|---------------------|
| `LoggingBehavior` | 1 | Log dispatch, duration, errors | None |
| `ValidationBehavior` | 2 | Run validators before handler | `dict[type, list[Callable]]` |
| `IdempotencyBehavior` | 3 | Skip duplicate commands | `ProcessedMessageStore` |
| `AggregateLockingBehavior` | 4 | Lock aggregate per command | `LockProvider` + `LockKeyResolver` |
| `EventIdempotencyBehavior` | 5 | Skip duplicate events | `ProcessedMessageStore` |

## Using Behaviors on Event Handlers

Pipeline behaviors also work on event handlers registered with `MessageBus.register_event()` or `EventBus.register()`. The same `PipelineBehavior` protocol applies:

```python
from pydomain.cqrs.behaviors import EventIdempotencyBehavior
from pydomain.testing import FakeProcessedMessageStore

store = FakeProcessedMessageStore()

bus.register_event(
    event_type=OrderPlacedIntegrationReceived,  # an event that arrives through an InboundEventGateway
    handler=SendConfirmationHandler(),
    behaviors=[
        LoggingBehavior(),
        EventIdempotencyBehavior(store),  # Skip duplicates on redelivery
    ],
)
```

Event-specific behaviors like `EventIdempotencyBehavior` check the event's `causation_id` for dedup, which is set deterministically by the `InboundEventGateway` for externally-sourced events. Register it for inbound events only: the events one in-process command raises share that command's `causation_id`, so only the first would reach the handler (a known limit, planned for a later release).

## Composing Behaviors Across Buses

Share behavior instances across Command and Query buses when they're stateless:

```python
logging_behavior = LoggingBehavior()
metrics_behavior = MetricsBehavior(metrics)

# Same behaviors, different buses
command_bus.register(PlaceOrder, handler, uow_factory,
    behaviors=[logging_behavior, metrics_behavior])
query_bus.register(GetOrder, handler,
    behaviors=[logging_behavior, metrics_behavior])
```

Share a behavior instance only when it holds no per-command state of its own. `IdempotencyBehavior` keeps its state in the `ProcessedMessageStore`, not in the behavior, so one instance (or several over one store) can serve many commands; a behavior that keeps counters or caches in its own attributes should get one instance per bus.

## See Also

- [Pipeline Behaviors concept](../../concepts/cqrs/pipeline-behaviors.md)
- [Add Idempotency](add-idempotency.md)
- [Add Distributed Locking](add-distributed-locking.md)
- [Configure the Command Bus](configure-command-bus.md)
