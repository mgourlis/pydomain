"""INF-05, INF-06, INF-07, INF-08 probes: real bootstrap()/Application."""

import asyncio
import logging
from uuid import UUID

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.bootstrap import bootstrap
from pydomain.infrastructure.message_broker import OutboundEventGateway
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.infrastructure.message_subscriber import InboundEventGateway
from pydomain.testing import (
    FakeEventStore,
    FakeOutboxStore,
    InMemoryMessageBroker,
    InMemoryMessageSubscriber,
)

logging.basicConfig(level=logging.CRITICAL)
ORDER = "00000000-0000-4000-8000-000000000001"


class ShipmentFailedIE(IntegrationEvent):
    order_id: str


class ShipmentFailed(DomainEvent):
    order_id: UUID


def translate(ie: ShipmentFailedIE) -> ShipmentFailed:
    return ShipmentFailed(order_id=UUID(ie.order_id))


class EagerSubscriber(InMemoryMessageSubscriber):
    """Like a Kafka consumer: start() begins delivering the backlog at once.
    ACK on normal return (offset commit), NACK on raise."""

    def __init__(self, backlog):
        super().__init__()
        self.backlog = backlog
        self.acks = 0
        self.nacks = 0

    async def start(self):
        await super().start()
        for topic, payload in self.backlog:
            try:
                await self.simulate_message(topic, payload)
                self.acks += 1
            except Exception:
                self.nacks += 1


async def inf05() -> None:
    print("--- INF-05 ---")
    bus = MessageBus()
    sub = EagerSubscriber([("shipping.failed", {"order_id": ORDER})])
    gw = InboundEventGateway(sub, bus)
    gw.register_translation("shipping.failed", ShipmentFailedIE, translate)
    app = await bootstrap(message_bus=bus, inbound_gateways=[gw])  # ADR-047 order
    handled = []

    async def handler(e: ShipmentFailed) -> None:
        handled.append(e)

    bus.register_event(ShipmentFailed, handler)  # "registered after bootstrap"
    print("INF-05 backlog message: acked =", sub.acks, "nacked =", sub.nacks, "handled =", len(handled))
    print("INF-05 Application has message_bus attr:", hasattr(app, "message_bus"),
          "| event_registry attr:", hasattr(app, "event_registry"))

    # Docs path (configure-inbound-event-gateway.md step 5): bootstrap(inbound_gateways=[gw])
    # with no message_bus -> Application gets a *different* bus than the gateway.
    bus2 = MessageBus()
    gw2 = InboundEventGateway(InMemoryMessageSubscriber(), bus2)
    app2 = await bootstrap(inbound_gateways=[gw2])
    print("INF-05 app bus is gateway bus (no message_bus passed):", app2._message_bus is bus2)


class FailingBroker(InMemoryMessageBroker):
    def __init__(self, fail_start=False, fail_stop=False):
        super().__init__()
        self.fail_start, self.fail_stop = fail_start, fail_stop
        self.stopped = False

    async def start(self):
        if self.fail_start:
            raise ConnectionError("broker unreachable")

    async def stop(self):
        self.stopped = True


async def inf06() -> None:
    print("--- INF-06 ---")
    sub = InMemoryMessageSubscriber()
    gw = InboundEventGateway(sub, MessageBus())
    try:
        await bootstrap(inbound_gateways=[gw], message_broker=FailingBroker(fail_start=True))
    except ConnectionError as exc:
        print("INF-06 bootstrap raised", type(exc).__name__, "| inbound subscriber started =",
              sub.started, "stopped =", sub.stopped, "| no Application returned to shut it down")


class FailingStopSubscriber(InMemoryMessageSubscriber):
    async def stop(self):
        raise RuntimeError("consumer close failed")


async def inf07() -> None:
    print("--- INF-07 ---")
    bad = InboundEventGateway(FailingStopSubscriber(), MessageBus())
    good_sub = InMemoryMessageSubscriber()
    good = InboundEventGateway(good_sub, MessageBus())
    broker = FailingBroker()
    store = FakeOutboxStore()
    outbound = OutboundEventGateway(store, broker, OutboundEventRegistry(), poll_interval_seconds=0.01)
    app = await bootstrap(inbound_gateways=[bad, good], message_broker=broker, outbound_gateway=outbound)
    await asyncio.sleep(0.02)
    try:
        await app.shutdown()
    except RuntimeError as exc:
        print("INF-07 shutdown raised:", exc)
    print("INF-07 after failed shutdown: 2nd inbound stopped =", good_sub.stopped,
          "| outbound task done =", outbound._task.done() if outbound._task else None,
          "| broker stopped =", broker.stopped)
    # clean up the leaked relay so the probe exits
    await outbound.stop()

    # Hung inbound stop -> shutdown hangs (no timeout)
    class HangingStop(InMemoryMessageSubscriber):
        async def stop(self):
            await asyncio.Event().wait()

    app2 = await bootstrap(inbound_gateways=[InboundEventGateway(HangingStop(), MessageBus())])
    try:
        await asyncio.wait_for(app2.shutdown(), 0.2)
        print("INF-07 hung stop: shutdown returned")
    except TimeoutError:
        print("INF-07 hung stop: shutdown still blocked after 0.2s (no internal timeout)")

    # Idempotency: shutdown twice
    sub3 = InMemoryMessageSubscriber()
    b3 = FailingBroker()
    app3 = await bootstrap(inbound_gateways=[InboundEventGateway(sub3, MessageBus())], message_broker=b3)
    await app3.shutdown()
    await app3.shutdown()
    print("INF-07 shutdown twice: no error (fakes are idempotent; library adds no guard)")


async def inf08() -> None:
    print("--- INF-08 ---")
    es = FakeEventStore()
    app = await bootstrap(event_store=es)
    attrs = [a for a in dir(app) if not a.startswith("__")]
    print("INF-08 Application public/private attrs:", attrs)
    print("INF-08 event_store reachable from app:", any(v is es for v in vars(app).values()))


async def main():
    await inf05()
    await inf06()
    await inf07()
    await inf08()


asyncio.run(main())
