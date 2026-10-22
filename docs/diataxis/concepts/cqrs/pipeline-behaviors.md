# Pipeline Behaviors

> **Adoption Level:** 2 — CQRS
> **Module:** `pydomain.cqrs.behaviors`

## What are Pipeline Behaviors?

**Pipeline Behaviors** are middleware that wraps message handlers in an onion (decorator) pattern. Each behavior runs **before and after** the handler, enabling cross-cutting concerns like logging, validation, locking, and idempotency without modifying handler code.

## The Onion Model

```
Request
  │
  └── Behavior 1 (outermost)
        └── Behavior 2
              └── Behavior 3 (innermost)
                    └── Terminal Handler
              └── Behavior 3 (after)
        └── Behavior 2 (after)
  └── Behavior 1 (after)
  │
Response
```

Behaviors are composed in registration order: the first behavior in the list is the outermost. Each receives a `next` callable to invoke the next layer.

## What `next()` Means in a Command Pipeline

In a command pipeline the terminal step belongs to the `CommandBus`: it runs your handler, then commits the unit of work, then sets `ctx.new_events`. So `next()` includes the commit, and every behavior's code after `next()` runs after it. The bus opens the unit of work before the first behavior and rolls it back, if it did not commit, after the last one returns.

| Point | What a behavior sees |
|---|---|
| Before `next()` | Inside the unit-of-work scope; nothing committed; `ctx.new_events` is empty |
| After `next()` returns, the handler reached | The handler ran and the commit succeeded; `ctx.new_events` holds the committed, stamped events |
| After `next()` returns, the handler not reached | An inner behavior short-circuited, for example `IdempotencyBehavior` returned a cached result: nothing was committed and nothing will be; the scope rolls back when the bus leaves it; `ctx.new_events` is empty |
| After `next()` raises, not committed | The handler or the commit failed; the rollback has not run yet: it runs when the bus leaves the scope |
| After `next()` raises, committed | The commit completed and an inner behavior raised after it, for example a lock release that re-raised a cancellation; no rollback runs; `ctx.new_events` holds the committed events, which are not dispatched to in-process event handlers |

An empty `ctx.new_events` does not tell you whether a commit happened: a command can commit without raising events. Writes a behavior makes through `ctx.uow` after `next()` are not committed; put them in the handler, or in a unit-of-work hook. Query and event pipelines have no commit: their `next()` only runs the handler.

A command pipeline's `next()` can be called once. A second call raises `PipelineReentryError` before the handler runs, so a behavior cannot retry the handler inside one dispatch; the bus wraps the error in `CommandExecutionError`, and a first run that already committed keeps its commit. To retry a failed command, dispatch it again, which opens a new scope. Query and event pipelines allow any number of `next()` calls.

## `PipelineBehavior` Protocol

```python
from pydomain.cqrs.behaviors import PipelineBehavior, MessageContext, NextHandler


class PipelineBehavior(Protocol):
    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        """Run logic before and/or after calling next()."""
        ...
```

## `MessageContext`

The context flows through every behavior and the terminal handler:

```python
@dataclass
class MessageContext:
    message: Any              # The command, query, or event
    handler: Callable | None  # The terminal handler
    kind: MessageKind         # COMMAND, QUERY, or EVENT
    uow: Any | None           # Unit of Work (commands only)
    correlation_id: UUID | None
    causation_id: UUID | None
    metadata: dict[str, Any]  # Arbitrary data for behaviors
    new_events: list[DomainEvent]
```

Behaviors can read from and write to `metadata` to pass data downstream.

## `MessageKind`

```python
class MessageKind(Enum):
    COMMAND = auto()
    EVENT = auto()
    QUERY = auto()
```

Behaviors can branch on `ctx.kind` to apply different logic per message type.

## Built-in Behaviors

### LoggingBehavior

Logs entry, success, and failure of message handling with wall-clock duration:

```python
from pydomain.cqrs.behaviors import LoggingBehavior

behavior = LoggingBehavior(
    payload_formatter=lambda msg: {"type": type(msg).__name__}
)
```

Uses `logging.getLogger("pydomain.pipeline")`. Logs at INFO for entry/success, ERROR for failures. For a command, a commit that fails is logged as a failure: the commit runs inside `next()`.

### ValidationBehavior

Runs registered validators before the handler. If any validator raises, the handler is skipped:

```python
from pydomain.cqrs.behaviors import ValidationBehavior

behavior = ValidationBehavior()
behavior.register(PlaceOrder, validate_order_items)
behavior.register(PlaceOrder, validate_customer_active)
```

Validators can be sync or async. They run in registration order.

### AggregateLockingBehavior

Acquires distributed locks for aggregate access, preventing concurrent modification of the same aggregate:

```python
from pydomain.cqrs.behaviors import AggregateLockingBehavior

behavior = AggregateLockingBehavior(
    provider=redis_lock_provider,
    resolver=DictLockKeyResolver(),
)
```

Locks are acquired in **sorted** order (deadlock prevention), held through the commit, and released in reverse order however the command ends, cancellation included; a failed release after a successful commit is logged, not raised (ADR-076, [Release Guarantees](idempotency-and-locking.md#release-guarantees)).

### IdempotencyBehavior

Caches and returns results for duplicate commands. Checks the `ProcessedMessageStore` before delegating to the inner handler:

```python
from pydomain.cqrs.behaviors import IdempotencyBehavior

behavior = IdempotencyBehavior(store=redis_processed_message_store)
```

If a command has already been processed, the cached result is returned immediately — the handler is never called and nothing is committed. Otherwise the result is recorded after the commit, so a command whose commit failed is not cached and runs again when retried. A record that fails after the commit is logged as a WARNING, and the result is still returned.

### EventIdempotencyBehavior

Skips duplicate event handling for events arriving from external message brokers (at-least-once delivery). Checks the `ProcessedMessageStore` keyed on the event's `causation_id`:

```python
from pydomain.cqrs.behaviors import EventIdempotencyBehavior

behavior = EventIdempotencyBehavior(store=redis_processed_message_store)
```

The dedup key is the event's `causation_id`, which for inbound events from the `InboundEventGateway` is set to the integration event's `event_id` — deterministic across redeliveries. Events without a `causation_id` always pass through.

> **Known limit:** use this behavior for inbound events only. The events one in-process command raises all carry that command's `causation_id`, so they share one key and only the first reaches the handler. A store shared with `IdempotencyBehavior` makes even the first one skipped. This is a known limit, planned for a later release.

Unlike `IdempotencyBehavior` (which caches command results), this behavior only marks the event as processed — event handlers return `None`, so there is no result to cache.

> **📌 ADR-062:** [EventBus Application Layer Peer](../../../adr/ADR-062-eventbus-application-layer-peer.md) documents the EventBus extraction and event idempotency design.

## Custom Behaviors

Any class implementing `PipelineBehavior` works. For example, you could add distributed tracing by implementing a behavior that creates OpenTelemetry spans:

```python
class TracingBehavior:
    """Minimal example — see the extension docs for the full implementation."""

    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        try:
            import opentelemetry.trace as otel_trace
        except ImportError:
            return await next()

        with otel_trace.get_tracer("myapp").start_as_current_span(
            f"{ctx.kind.name} {type(ctx.message).__name__}"
        ) as span:
            span.set_attribute("message.type", type(ctx.message).__name__)
            return await next()
```

This is a common extension that also involves wrapping the `MessageBroker` and `MessageSubscriber`. See the [Distributed Tracing extension example](../infrastructure/tracing.md) for the complete copyable implementation.

Another example — a metrics collection behavior:

```python
class MetricsBehavior:
    def __init__(self, metrics: MetricsCollector) -> None:
        self._metrics = metrics

    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        with self._metrics.timer(f"cqrs.{ctx.kind.name.lower()}"):
            result = await next()
        self._metrics.increment(
            f"cqrs.{type(ctx.message).__name__}.success"
        )
        return result
```

## Pipeline Slot Order

The recommended behavior ordering (slots count from 1, outermost first; command and event pipelines are separate, so event behaviors are a second list, not slot 5 of a command pipeline):

| Slot | Behavior | Scope | Purpose |
|------|----------|-------|---------|
| 1 | `LoggingBehavior` | Command / Query / Event | Log every dispatch |
| 2 | `ValidationBehavior` | Command / Query | Fail fast on invalid input |
| 3 | `IdempotencyBehavior` | Command | Skip already-processed commands |
| 4 | `AggregateLockingBehavior` | Command | Prevent concurrent aggregate access |
| 5 | `EventIdempotencyBehavior` | Event | Skip duplicate events from broker redelivery |
| — | Terminal handler | — | Your business logic |

> **Known limit:** with idempotency before locking, two concurrent dispatches of the same `command_id` can both run the handler. This is a known limit, planned for a later release.

If you add a custom tracing behavior (see the [Distributed Tracing extension example](../infrastructure/tracing.md)), place it outermost — slot 0 — so it captures every downstream concern including logging and validation.

> **📌 ADR-062:** [EventBus Application Layer Peer](../../../adr/ADR-062-eventbus-application-layer-peer.md) documents the EventBus extraction and event idempotency design.

## Next Steps

- **[Add a Pipeline Behavior →](../../how-to/cqrs/add-pipeline-behavior.md)** — step-by-step guide
- **[Implement Distributed Tracing →](../../how-to/infrastructure/configure-tracing.md)** — add tracing to your project as an extension
- **[Add Idempotency →](../../how-to/cqrs/add-idempotency.md)** — idempotency configuration
- **[Add Distributed Locking →](../../how-to/cqrs/add-distributed-locking.md)** — locking setup
- **[Idempotency & Locking →](idempotency-and-locking.md)** — protocol details
