# Extending the Library — Distributed Tracing as an Extension Pattern

<!-- docs-test: runnable -->

<!-- docs-test: setup
from typing import Any
-->

> **Adoption Level:** 3 — Infrastructure (extension example)
> **Extension Points:** `PipelineBehavior`, `MessageBroker`, `MessageSubscriber`

## What This Pattern Shows

The library provides three **extension points** — protocols that you can implement to add cross-cutting behaviour without modifying library code:

- **`PipelineBehavior`** — wraps command, query, and event handlers in-process
- **`MessageBroker`** — outbound message publishing
- **`MessageSubscriber`** — inbound message receiving

**Distributed tracing** (OpenTelemetry) is a common cross-cutting concern that exercises all three extension points. This page shows you exactly how to implement it — the complete, copyable code — so you can adapt it to your own project.

The library's own `correlation_id`/`causation_id` fields (on `DomainEvent` and `IntegrationEvent`) model the **domain-level causation chain** — what command triggered this event? Distributed tracing (W3C `traceparent`) models the **operations-level trace** — what span is this in my observability tool? They are complementary, not alternatives, and this extension covers both.

## The Three Extension Points

### 1. `PipelineBehavior` — In-Process Spans

The `PipelineBehavior` protocol wraps every dispatched message before and after the handler runs:

```python
from pydomain.cqrs.behaviors import PipelineBehavior, MessageContext, NextHandler


class TracingBehavior:
    """Creates an OTel span for every dispatched command, query, or event."""

    def __init__(self, tracer_name: str = "myapp") -> None:
        self._tracer_name = tracer_name

    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        try:
            import opentelemetry.trace as otel_trace
        except ImportError:
            return await next()  # graceful degradation

        tracer = otel_trace.get_tracer(self._tracer_name)
        span_name = f"{ctx.kind.name} {type(ctx.message).__name__}"

        with tracer.start_as_current_span(span_name) as span:
            span.set_attribute("message.type", type(ctx.message).__name__)
            span.set_attribute("message.kind", ctx.kind.name.lower())
            if ctx.correlation_id is not None:
                span.set_attribute("messaging.correlation_id", str(ctx.correlation_id))
            if ctx.causation_id is not None:
                span.set_attribute("messaging.causation_id", str(ctx.causation_id))

            ctx.metadata["otel_span"] = span
            try:
                return await next()
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(otel_trace.Status(otel_trace.StatusCode.ERROR))
                raise
```

Register it like any other pipeline behavior:

<!-- docs-test: skip -->
```python
bus.register_command(PlaceOrder, handler,
    behaviors=[TracingBehavior(tracer_name="orders"), LoggingBehavior()])
```

### 2. `MessageBroker` — Outbound Trace Propagation

Wrap a `MessageBroker` to inject W3C `traceparent` into broker message headers before publishing. Keep the headers the caller passes: the [outbox relay](outbound-event-gateway.md) publishes a stamped event with `headers={"correlation_id": ..., "causation_id": ...}`, and a wrapper that drops them, or whose `publish()` takes no `**kwargs`, breaks every delivery ([ADR-074](../../../adr/ADR-074-outbox-relay-and-registry-honour-their-contracts.md)):

```python
from typing import Any

from pydomain.infrastructure.message_broker import MessageBroker
from pydomain.cqrs.integration_events import IntegrationEvent


class TracingPublisherMiddleware:
    """Wraps a MessageBroker to inject W3C traceparent headers."""

    def __init__(self, broker: MessageBroker) -> None:
        self._broker = broker

    async def publish(
        self, topic: str, event: IntegrationEvent, **kwargs: Any
    ) -> None:
        # Keep the headers the caller passed: the outbox relay sends the
        # entry's correlation_id and causation_id this way (ADR-074).
        headers: dict[str, str] = dict(kwargs.pop("headers", None) or {})

        try:
            import opentelemetry.trace as otel_trace
            from opentelemetry.trace.propagation.tracecontext import (
                TraceContextTextMapPropagator,
            )

            span = otel_trace.get_current_span()
            ctx = otel_trace.set_span_in_context(span)
            TraceContextTextMapPropagator().inject(headers, ctx)
        except ImportError:
            pass

        if headers:
            await self._broker.publish(topic, event, headers=headers, **kwargs)
        else:
            await self._broker.publish(topic, event, **kwargs)
```

<!-- docs-test: check
from pydomain.cqrs.integration_events import IntegrationEvent as _Event
from pydomain.testing import InMemoryMessageBroker

_inner = InMemoryMessageBroker()
await TracingPublisherMiddleware(_inner).publish(
    "orders.shipped", _Event(), headers={"correlation_id": "c-1", "causation_id": "k-1"}
)
assert _inner.published[0][2]["correlation_id"] == "c-1"
assert _inner.published[0][2]["causation_id"] == "k-1"
-->

### 3. `MessageSubscriber` — Inbound Trace Propagation

Wrap a `MessageSubscriber` to extract `traceparent` from broker headers and continue the trace:

```python
from collections.abc import Awaitable, Callable
from typing import Any

from pydomain.infrastructure.message_subscriber import MessageSubscriber


class TracingSubscriberMiddleware:
    """Wraps a MessageSubscriber to extract and continue W3C trace context."""

    def __init__(
        self,
        subscriber: MessageSubscriber,
        *,
        header_key: str = "__trace_headers",
    ) -> None:
        self._subscriber = subscriber
        self._header_key = header_key

    def subscribe(
        self,
        topic: str,
        handler: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        async def _traced(payload: dict[str, Any]) -> None:
            headers: dict[str, str] = payload.pop(self._header_key, {})

            try:
                import opentelemetry.trace as otel_trace
                from opentelemetry.trace.propagation.tracecontext import (
                    TraceContextTextMapPropagator,
                )
                from opentelemetry import context as otel_context

                ctx = TraceContextTextMapPropagator().extract(carrier=headers)
                token = otel_context.attach(ctx)
                try:
                    with otel_trace.get_tracer(__name__).start_as_current_span(
                        f"process {topic}",
                    ) as span:
                        span.set_attribute("messaging.topic", topic)
                        await handler(payload)
                finally:
                    otel_context.detach(token)
            except ImportError:
                await handler(payload)

        self._subscriber.subscribe(topic, _traced)
```

Your concrete `MessageSubscriber` implementation must place broker-level trace headers into the payload dict under `__trace_headers` before invoking the handler — this is the only contract between your subscriber and this middleware.

## Complete Wiring Example

<!-- docs-test: skip -->
```python
from pydomain.cqrs import CommandBus, LoggingBehavior
from pydomain.infrastructure import InboundEventGateway, MessageBus
from myapp.tracing import (               # <-- your extension code
    TracingBehavior,
    TracingPublisherMiddleware,
    TracingSubscriberMiddleware,
)

# ── Bus with in-process tracing ─────────────────────────────────
tracing = TracingBehavior(tracer_name="order-service")
bus = MessageBus(command_bus=command_bus, query_bus=query_bus)

bus.register_command(PlaceOrder, handler,
    behaviors=[tracing, LoggingBehavior()])
bus.register_event(OrderPlaced, event_handler,
    behaviors=[tracing, LoggingBehavior()])

# ── Broker with cross-service tracing ───────────────────────────
raw_broker = RabbitMQBroker("amqp://localhost")
broker = TracingPublisherMiddleware(raw_broker)

raw_subscriber = RabbitMQSubscriber("amqp://localhost")
subscriber = TracingSubscriberMiddleware(raw_subscriber)

gateway = InboundEventGateway(subscriber, bus)
gateway.register_translation(
    "orders.placed",
    OrderPlacedIntegrationEvent,
    translate_order_placed,
)

await broker.start()
await subscriber.start()
```

## Design Considerations

### Graceful Degradation

All three components degrade gracefully when `opentelemetry-api` is not installed — they pass through without creating or propagating spans. Install it when you're ready:

```bash
pip install opentelemetry-api opentelemetry-sdk
```

### No Trace Fields in Event Schemas

`traceparent` is a transport-level concern. Putting it on `IntegrationEvent` would couple your domain schema to a specific observability protocol. Instead, tracing flows through:

- **Broker headers** (outbound via `TracingPublisherMiddleware`, next to the relay's `correlation_id` and `causation_id` headers)
- **Payload convention key** (inbound via `TracingSubscriberMiddleware`)
- **Pipeline metadata** (in-process via `TracingBehavior`)

### Domain Tracing vs Distributed Tracing

The library ships `correlation_id` and `causation_id` on `DomainEvent` and `IntegrationEvent` as built-in fields. On the outbound path the [outbox relay](outbound-event-gateway.md) applies them for you: an outbox entry records the domain event's `correlation_id` and, as `causation_id`, the domain event's `event_id`; at delivery the relay stamps both onto the integration event, overriding whatever the translator set, and also passes them as `correlation_id` and `causation_id` message headers. For an unstamped domain event the relay does not stamp the event and passes no headers ([ADR-074](../../../adr/ADR-074-outbox-relay-and-registry-honour-their-contracts.md)). These model **business-level causation** — they are useful for idempotency, auditing, and domain reasoning. The OpenTelemetry tracing shown here models **operations-level tracing** — useful for debugging latency, error propagation, and visualizing service topology.

They complement each other. Use both.

### Choosing Layers

| If you need... | Implement... |
|----------------|--------------|
| Traces within one service | `TracingBehavior` only |
| Traces across service boundaries | All three components |
| Sentry error linking | `TracingBehavior` + Sentry OTel integration (`experiments={"otel_powered_span": True}`) |
| No tracing at all | None — everything is optional |

## Relationship to Other Concepts

| Concept | Relationship |
|---------|--------------|
| [Pipeline Behaviors](../cqrs/pipeline-behaviors.md) | `TracingBehavior` implements the `PipelineBehavior` protocol |
| [Message Broker](message-broker.md) | `TracingPublisherMiddleware` wraps any `MessageBroker` |
| [MessageSubscriber](message-subscriber.md) | `TracingSubscriberMiddleware` wraps any `MessageSubscriber` |
| [InboundEventGateway](inbound-event-gateway.md) | Gateway dispatches through `MessageBus` where `TracingBehavior` runs |
| `correlation_id` / `causation_id` | Domain-level causation — separate from W3C traceparent |
| [Integration Events](../cqrs/integration-events.md) | Cross-boundary event contracts carry domain tracing fields |

## Next Steps

- **[How to Implement Distributed Tracing →](../../how-to/infrastructure/configure-tracing.md)** — step-by-step setup guide
- **[Add a Pipeline Behavior →](../../how-to/cqrs/add-pipeline-behavior.md)** — understand the behavior system
- **[Configure a Message Broker →](../../how-to/infrastructure/configure-message-broker.md)** — production broker setup
- **[Integration Events →](../cqrs/integration-events.md)** — domain tracing on integration events
