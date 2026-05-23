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
              └── Handler
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
| `IdempotencyBehavior` | 3 | Skip duplicate commands | `ProcessedCommandStore` |
| `AggregateLockingBehavior` | 4 | Lock aggregate per command | `LockProvider` + `LockKeyResolver` |

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

Stateful behaviors (like `IdempotencyBehavior`) should not be shared.

## See Also

- [Pipeline Behaviors concept](../../concepts/cqrs/pipeline-behaviors.md)
- [Add Idempotency](add-idempotency.md)
- [Add Distributed Locking](add-distributed-locking.md)
- [Configure the Command Bus](configure-command-bus.md)
