# How to Implement Distributed Tracing in Your Own Project

<!-- docs-test: runnable -->

> **Prerequisites:** [Extension Pattern — Distributed Tracing](../../concepts/infrastructure/tracing.md), [Pipeline Behaviors](../../concepts/cqrs/pipeline-behaviors.md), [Configure a Message Broker](configure-message-broker.md)
> **Optional dependency:** `pip install opentelemetry-api opentelemetry-sdk`

## Problem

You want to trace a request across your event-driven system — from the initial HTTP command through domain events, integration event publishing, and consumption by another service — so you can visualise the full flow in Jaeger, Sentry, or Zipkin.

The library provides the extension points (`PipelineBehavior`, `MessageBroker`, `MessageSubscriber` protocols). This guide shows you how to implement tracing on top of them in your own project.

## 1. Create the `TracingBehavior` Class

Create a file in your project — e.g. `myapp/tracing.py` — and add the in-process tracing behavior that wraps every dispatched command, query, or event in an OpenTelemetry span:

```python
# myapp/tracing.py

from typing import Any

from pydomain.cqrs.behaviors import PipelineBehavior, MessageContext, NextHandler


class TracingBehavior:
    """Creates an OTel span for every dispatched command, query, or event.

    Degrades gracefully when opentelemetry-api is not installed.
    """

    def __init__(self, tracer_name: str = "myapp") -> None:
        self._tracer_name = tracer_name

    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        try:
            import opentelemetry.trace as otel_trace
        except ImportError:
            return await next()

        tracer = otel_trace.get_tracer(self._tracer_name)
        span_name = f"{ctx.kind.name} {type(ctx.message).__name__}"

        with tracer.start_as_current_span(span_name) as span:
            span.set_attribute("message.type", type(ctx.message).__name__)
            span.set_attribute("message.kind", ctx.kind.name.lower())
            if ctx.correlation_id is not None:
                span.set_attribute(
                    "messaging.correlation_id", str(ctx.correlation_id)
                )
            if ctx.causation_id is not None:
                span.set_attribute(
                    "messaging.causation_id", str(ctx.causation_id)
                )

            ctx.metadata["otel_span"] = span
            try:
                return await next()
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(
                    otel_trace.Status(otel_trace.StatusCode.ERROR)
                )
                raise
```

The span names appear as `COMMAND PlaceOrder`, `EVENT OrderPlaced`, etc. in your tracing tool.

## 2. Create the Publisher Middleware

In the same file, add the middleware that injects W3C Trace Context headers when publishing to the broker:

```python
# myapp/tracing.py (continued)

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

## 3. Create the Subscriber Middleware

Still in the same file, add the middleware that extracts trace context on inbound messages:

```python
# myapp/tracing.py (continued)

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

> **Concrete subscriber implementation note:** Your `MessageSubscriber` implementation must place broker trace headers into the payload dict under `__trace_headers` (or a custom key set via `header_key` parameter). This is the only contract between your subscriber and the middleware.

<!-- docs-test: check
from pydomain.cqrs.behaviors import MessageContext
from pydomain.testing import InMemoryMessageSubscriber


async def _next() -> str:
    return "handled"


assert await TracingBehavior().handle(MessageContext(message=object()), _next) == "handled"

_received: list[dict[str, Any]] = []


async def _record(payload: dict[str, Any]) -> None:
    _received.append(payload)


_subscriber = InMemoryMessageSubscriber()
TracingSubscriberMiddleware(_subscriber).subscribe("orders.shipped", _record)
await _subscriber.simulate_message(
    "orders.shipped", {"order_id": "o-1", "__trace_headers": {}}
)
assert _received == [{"order_id": "o-1"}]
-->

## 4. Wire Everything Together

In your composition root, wire all three components:

<!-- docs-test: skip -->
```python
from myapp.tracing import (                # <-- your extension module
    TracingBehavior,
    TracingPublisherMiddleware,
    TracingSubscriberMiddleware,
)
from pydomain.cqrs import CommandBus, LoggingBehavior
from pydomain.infrastructure import InboundEventGateway, MessageBus

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

## 5. Install OTel (When Ready)

The tracing code degrades gracefully without OTel installed. When you're ready to see traces:

```bash
pip install opentelemetry-api opentelemetry-sdk
```

Configure an OTel exporter (e.g., Jaeger, Zipkin, or the console exporter for development):

<!-- docs-test: skip -->
```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
trace.set_tracer_provider(provider)
```

## Expected Outcome

With both in-process and cross-service tracing in place, a trace in Jaeger or Sentry will show:

```
POST /orders                          ← HTTP request (otel-instrumented)
  └── COMMAND PlaceOrder              ← TracingBehavior span
        └── EVENT OrderPlaced         ← TracingBehavior span
              └── publish to broker   ← TracingPublisherMiddleware injects traceparent
                    └── consume       ← TracingSubscriberMiddleware continues trace
                          └── EVENT ExternalOrderPlaced  ← TracingBehavior (consumer)
```

All spans share the same `trace_id` — a single connected trace across the service boundary.

## Choosing Tracing Layers

| If you need... | Implement... |
|----------------|--------------|
| Traces within one service | `TracingBehavior` only |
| Traces across service boundaries | All three components |
| Sentry error linking | `TracingBehavior` + Sentry OTel integration |
| No tracing at all | None — everything is optional |

## Next Steps

- **[Extension Pattern — Distributed Tracing](../../concepts/infrastructure/tracing.md)** — deeper explanation of the three-layer model and design decisions
- **[Pipeline Behaviors](../../concepts/cqrs/pipeline-behaviors.md)** — how your `TracingBehavior` works with other behaviors
- **[Configure a Message Broker](configure-message-broker.md)** — set up the underlying broker
- **[Configure an InboundEventGateway](configure-inbound-event-gateway.md)** — bridge external messages to the bus

## Cross-References

- **ADR-021**: [Correlation/Causation Propagation via UoW Stamping](../../../adr/ADR-021-correlation-causation-propagation.md) — domain-level tracing
- **ADR-061**: [Integration Event Distributed Tracing](../../../adr/ADR-061-integration-event-tracing.md) — correlation/causation on `IntegrationEvent`
