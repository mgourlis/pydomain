"""Probes for the _Run lifecycle (B-72): start/stop races, concurrent stops, a dead task, direct run()."""
import asyncio
from typing import Any

from pydomain.cqrs.outbox import OutboundEventRegistry, OutboxEntry
from pydomain.infrastructure.message_broker import OutboundEventGateway
from pydomain.testing import FakeOutboxStore


class HoldingBroker:
    def __init__(self):
        self.gate = asyncio.Event()
        self.in_flight = 0
        self.calls = 0

    async def publish(self, topic, event, **kw: Any):
        self.calls += 1
        self.in_flight += 1
        try:
            await self.gate.wait()
        finally:
            self.in_flight -= 1

    async def start(self): ...
    async def stop(self): ...


def gw(store, broker):
    from pydomain.cqrs.integration_events import IntegrationEvent
    from pydomain.ddd.domain_event import DomainEvent

    class D(DomainEvent): ...
    class I(IntegrationEvent): ...
    r = OutboundEventRegistry()
    r.register_translation(D, I, "t", lambda e: I())
    return OutboundEventGateway(store, broker, r, poll_interval_seconds=0.005), I


async def entry(store, I):
    from datetime import UTC, datetime
    ev = I()
    await store.append([OutboxEntry(message_id=ev.event_id, topic="t", payload=ev.model_dump(), occurred_at=datetime.now(UTC))])


async def concurrent_stops_both_wait():
    store, broker = FakeOutboxStore(), HoldingBroker()
    g, I = gw(store, broker)
    await entry(store, I)
    await g.start()
    while broker.in_flight == 0:
        await asyncio.sleep(0.001)
    first = asyncio.create_task(g.stop())
    await asyncio.sleep(0)
    second = asyncio.create_task(g.stop())
    await asyncio.sleep(0.02)
    print("second stop() returned while publish in flight?", second.done() and broker.in_flight > 0,
          "| first done?", first.done())
    broker.gate.set()
    await asyncio.gather(first, second)


async def start_during_stop():
    store, broker = FakeOutboxStore(), HoldingBroker()
    g, I = gw(store, broker)
    await entry(store, I)
    await g.start()
    while broker.in_flight == 0:
        await asyncio.sleep(0.001)
    s = asyncio.create_task(g.stop())
    await asyncio.sleep(0)
    await g.start()
    await entry(store, I)
    broker.gate.set()
    await s
    await asyncio.sleep(0.05)
    print("start during stop: new relay running?", g._task is not None and not g._task.done(), "publishes", broker.calls)
    await g.stop()


async def dead_task_then_start():
    store = FakeOutboxStore()

    class Dying(HoldingBroker):
        async def publish(self, topic, event, **kw):
            raise asyncio.CancelledError

    g, I = gw(store, Dying())
    await entry(store, I)
    await g.start()
    await asyncio.sleep(0.02)
    print("dead task: done?", g._task.done())
    await g.start()
    print("start after death without stop is no-op (documented gap)?", g._task.done())
    await g.stop()
    await g.start()
    print("start after stop: running?", not g._task.done())
    await g.stop()


async def start_then_direct_run_then_stop():
    store, broker = FakeOutboxStore(), HoldingBroker()
    broker.gate.set()
    g, I = gw(store, broker)
    await g.start()
    direct = asyncio.create_task(g.run())
    await asyncio.sleep(0.01)
    try:
        await asyncio.wait_for(g.stop(), 0.5)
        print("start + direct run + stop: stop returned; direct done?", direct.done(), "; any still running")
    except TimeoutError:
        print("start + direct run + stop: stop() HUNG")
    direct.cancel()


async def stop_after_task_cancelled_externally():
    store, broker = FakeOutboxStore(), HoldingBroker()
    g, I = gw(store, broker)
    await g.start()
    await asyncio.sleep(0.01)
    g._task.cancel()
    await asyncio.sleep(0.01)
    await g.stop()
    await g.start()
    print("restart after external cancel + stop: running?", not g._task.done())
    await g.stop()


async def main():
    await concurrent_stops_both_wait()
    await start_during_stop()
    await dead_task_then_start()
    await start_then_direct_run_then_stop()
    await stop_after_task_cancelled_externally()

asyncio.run(main())
