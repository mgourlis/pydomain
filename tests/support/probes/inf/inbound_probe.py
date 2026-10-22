"""INF-01, INF-02, INF-03, INF-04 probes: inbound gateway with real MessageBus."""

import asyncio
import logging
from uuid import UUID

from pydomain.cqrs.behaviors import EventIdempotencyBehavior
from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.event_registry import GenericDomainEvent
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.infrastructure.message_subscriber import InboundEventGateway
from pydomain.testing import FakeProcessedMessageStore, InMemoryMessageSubscriber

logging.basicConfig(level=logging.CRITICAL)


class ShipmentFailedIE(IntegrationEvent):
    order_id: str


class ShipmentFailed(DomainEvent):
    order_id: UUID


def translate(ie: ShipmentFailedIE) -> ShipmentFailed:
    return ShipmentFailed(order_id=UUID(ie.order_id))


ORDER = "00000000-0000-4000-8000-000000000001"


class AckNackSubscriber:
    """Minimal protocol-conforming subscriber: ACK on return, NACK on raise.
    Appends handlers per topic (as Kafka/RabbitMQ consumer libraries do)."""

    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}
        self.outcomes: list[str] = []

    def subscribe(self, topic, handler) -> None:
        self.handlers.setdefault(topic, []).append(handler)

    async def deliver(self, topic, payload) -> None:
        for h in self.handlers.get(topic, []):
            try:
                await h(payload)
                self.outcomes.append("ACK")
            except Exception:
                self.outcomes.append("NACK")

    async def start(self) -> None: ...
    async def stop(self) -> None: ...


async def inf01() -> None:
    print("--- INF-01 ---")
    bus = MessageBus()
    calls = []

    async def failing(event: ShipmentFailed) -> None:
        calls.append(event.event_id)
        raise RuntimeError("transient DB outage")

    bus.register_event(ShipmentFailed, failing)
    sub = AckNackSubscriber()
    gw = InboundEventGateway(sub, bus)
    gw.register_translation("shipping.failed", ShipmentFailedIE, translate)
    await sub.deliver("shipping.failed", {"order_id": ORDER})
    print("INF-01 handler calls:", len(calls), "subscriber outcome:", sub.outcomes)

    # Same with the shipped fake: does simulate_message raise?
    fake = InMemoryMessageSubscriber()
    gw2 = InboundEventGateway(fake, bus)
    gw2.register_translation("shipping.failed", ShipmentFailedIE, translate)
    try:
        await fake.simulate_message("shipping.failed", {"order_id": ORDER})
        print("INF-01 InMemoryMessageSubscriber.simulate_message raised: NO")
    except Exception as exc:  # noqa: BLE001
        print("INF-01 InMemoryMessageSubscriber.simulate_message raised:", type(exc).__name__)

    # Aggravation: EventIdempotencyBehavior marks processed BEFORE the handler,
    # so even an at-least-once redelivery would be skipped after a failure.
    bus3 = MessageBus()
    calls3 = []

    async def failing3(event: ShipmentFailed) -> None:
        calls3.append(1)
        raise RuntimeError("transient")

    bus3.register_event(
        ShipmentFailed, failing3, behaviors=[EventIdempotencyBehavior(FakeProcessedMessageStore())]
    )
    sub3 = AckNackSubscriber()
    gw3 = InboundEventGateway(sub3, bus3)
    gw3.register_translation("shipping.failed", ShipmentFailedIE, translate)
    payload = {
        "order_id": ORDER,
        "event_id": "00000000-0000-7000-8000-0000000000aa",
        "correlation_id": "00000000-0000-7000-8000-0000000000bb",
    }
    await sub3.deliver("shipping.failed", payload)
    await sub3.deliver("shipping.failed", payload)  # broker redelivery
    print("INF-01b failing handler with EventIdempotencyBehavior: calls over 2 deliveries =",
          len(calls3), "outcomes", sub3.outcomes)


async def inf02() -> None:
    print("--- INF-02 ---")
    store = FakeProcessedMessageStore()
    bus = MessageBus()
    seen = []

    async def handler(event: ShipmentFailed) -> None:
        seen.append((event.causation_id, event.correlation_id))

    bus.register_event(ShipmentFailed, handler, behaviors=[EventIdempotencyBehavior(store)])
    fake = InMemoryMessageSubscriber()
    gw = InboundEventGateway(fake, bus)
    gw.register_translation("shipping.failed", ShipmentFailedIE, translate)

    # Case A: event_id present, no correlation_id -> redelivered twice
    p_a = {"order_id": ORDER, "event_id": "00000000-0000-7000-8000-0000000000a1"}
    await fake.simulate_message("shipping.failed", p_a)
    await fake.simulate_message("shipping.failed", p_a)
    print("INF-02 case A (event_id, no correlation_id): handler ran", len(seen), "times; causation_ids:",
          [s[0] for s in seen])

    # Case B: correlation_id present, no event_id -> redelivered twice
    seen.clear()
    p_b = {"order_id": ORDER, "correlation_id": "00000000-0000-7000-8000-0000000000c1"}
    await fake.simulate_message("shipping.failed", p_b)
    await fake.simulate_message("shipping.failed", p_b)
    print("INF-02 case B (correlation_id, no event_id): handler ran", len(seen), "times; distinct causation_ids:",
          len({s[0] for s in seen}))


async def inf03() -> None:
    print("--- INF-03 ---")
    bus = MessageBus()
    calls = []

    async def handler(event: ShipmentFailed) -> None:
        calls.append(1)

    bus.register_event(ShipmentFailed, handler)
    sub = AckNackSubscriber()
    gw = InboundEventGateway(sub, bus)
    gw.register_translation("shipping.failed", ShipmentFailedIE, translate)
    gw.register_translation("shipping.failed", ShipmentFailedIE, translate)  # "hot-swap"
    await sub.deliver("shipping.failed", {"order_id": ORDER})
    print("INF-03 appending subscriber: subscriptions =", len(sub.handlers["shipping.failed"]),
          "handler calls for ONE message =", len(calls))
    fake = InMemoryMessageSubscriber()
    calls.clear()
    gw2 = InboundEventGateway(fake, bus)
    gw2.register_translation("shipping.failed", ShipmentFailedIE, translate)
    gw2.register_translation("shipping.failed", ShipmentFailedIE, translate)
    await fake.simulate_message("shipping.failed", {"order_id": ORDER})
    print("INF-03 InMemoryMessageSubscriber: handler calls for ONE message =", len(calls))


async def inf04() -> None:
    print("--- INF-04 ---")
    bus = MessageBus()
    try:
        await bus.dispatch(GenericDomainEvent(type="LegacyThing", data={"x": 1}))  # type: ignore[arg-type]
        print("INF-04 GenericDomainEvent dispatch: OK")
    except TypeError as exc:
        print("INF-04 GenericDomainEvent dispatch: TypeError:", exc)
    print("INF-04 issubclass(GenericDomainEvent, DomainEvent) =", issubclass(GenericDomainEvent, DomainEvent))

    class Base(DomainEvent):
        pass

    class Child(Base):
        pass

    hits = []

    async def on_base(e: DomainEvent) -> None:
        hits.append(type(e).__name__)

    bus.register_event(Base, on_base)
    bus.register_event(DomainEvent, on_base)
    await bus.dispatch(Child())
    print("INF-04 handlers on Base and DomainEvent, dispatch Child -> hits:", hits)


async def main() -> None:
    await inf01()
    await inf02()
    await inf03()
    await inf04()


asyncio.run(main())
