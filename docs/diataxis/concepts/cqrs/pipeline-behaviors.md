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

Uses `logging.getLogger("pydomain.pipeline")`. Logs at INFO for entry/success, ERROR for failures.

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

Locks are acquired in **sorted** order (deadlock prevention) and released in reverse order however the command ends, cancellation included; a failed release after a successful handler is logged, not raised (ADR-076, [Release Guarantees](idempotency-and-locking.md#release-guarantees)).

### IdempotencyBehavior

Caches and returns results for duplicate commands. Checks the `ProcessedMessageStore` before delegating to the inner handler:

```python
from pydomain.cqrs.behaviors import IdempotencyBehavior

behavior = IdempotencyBehavior(store=redis_processed_message_store)
```

If a command has already been processed, the cached result is returned immediately — the handler is never called.

### EventIdempotencyBehavior

Skips duplicate event handling for events arriving from external message brokers (at-least-once delivery). Checks the `ProcessedMessageStore` keyed on the event's `causation_id`:

```python
from pydomain.cqrs.behaviors import EventIdempotencyBehavior

behavior = EventIdempotencyBehavior(store=redis_processed_message_store)
```

The dedup key is the event's `causation_id`, which for inbound events from the `InboundEventGateway` is set to the integration event's `event_id` — deterministic across redeliveries. Events without a `causation_id` always pass through.

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

The recommended behavior ordering:

| Slot | Behavior | Scope | Purpose |
|------|----------|-------|---------|
| 0 | `LoggingBehavior` | Command / Query / Event | Log every dispatch |
| 1 | `ValidationBehavior` | Command / Query | Fail fast on invalid input |
| 2 | `IdempotencyBehavior` | Command | Skip already-processed commands |
| 3 | `AggregateLockingBehavior` | Command | Prevent concurrent aggregate access |
| 4 | `EventIdempotencyBehavior` | Event | Skip duplicate events from broker redelivery |
| — | Terminal handler | — | Your business logic |

If you add a custom tracing behavior (see the [Distributed Tracing extension example](../infrastructure/tracing.md)), place it outermost — slot 0 — so it captures every downstream concern including logging and validation.

> **📌 ADR-062:** [EventBus Application Layer Peer](../../../adr/ADR-062-eventbus-application-layer-peer.md) documents the EventBus extraction and event idempotency design.

## Next Steps

- **[Add a Pipeline Behavior →](../../how-to/cqrs/add-pipeline-behavior.md)** — step-by-step guide
- **[Implement Distributed Tracing →](../../how-to/infrastructure/configure-tracing.md)** — add tracing to your project as an extension
- **[Add Idempotency →](../../how-to/cqrs/add-idempotency.md)** — idempotency configuration
- **[Add Distributed Locking →](../../how-to/cqrs/add-distributed-locking.md)** — locking setup
- **[Idempotency & Locking →](idempotency-and-locking.md)** — protocol details
