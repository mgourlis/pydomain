"""Probes for the _stopping map: stop() from inside the relay's own task, a dead task's exception, and a cancelled stop()."""
import asyncio
import gc
from datetime import UTC, datetime

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry, OutboxEntry
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.message_broker import OutboundEventGateway
from pydomain.testing import FakeOutboxStore


class D(DomainEvent): ...
class I(IntegrationEvent): ...


def make(broker):
    r = OutboundEventRegistry()
    r.register_translation(D, I, "t", lambda e: I())
    store = FakeOutboxStore()
    return store, OutboundEventGateway(store, broker, r, poll_interval_seconds=0.005)


async def add(store):
    ev = I()
    await store.append([OutboxEntry(message_id=ev.event_id, topic="t", payload=ev.model_dump(), occurred_at=datetime.now(UTC))])


async def self_stop():
    holder = {}

    class SelfStopping:
        async def publish(self, topic, event, **kw):
            await holder["g"].stop()   # e.g. a broker callback asking the relay to stop
        async def start(self): ...
        async def stop(self): ...

    store, g = make(SelfStopping())
    holder["g"] = g
    await add(store)
    await g.start()
    task = g._task
    await asyncio.sleep(0.3)
    print("stop() called from inside the relay's own publish: relay task still pending after 0.3s?", not task.done(),
          "| detached runs still waiting:", len(g._stopping))
    task.cancel()


async def cancelled_stop_then_stop():
    gate = asyncio.Event()

    class Holding:
        async def publish(self, topic, event, **kw):
            await gate.wait()
        async def start(self): ...
        async def stop(self): ...

    store, g = make(Holding())
    await add(store)
    await g.start()
    await asyncio.sleep(0.02)
    s = asyncio.create_task(g.stop())
    await asyncio.sleep(0.01)
    s.cancel()
    try:
        await s
    except asyncio.CancelledError:
        pass
    second = asyncio.create_task(g.stop())
    await asyncio.sleep(0.02)
    print("after a cancelled stop(), a later stop() still waits for the run?", not second.done())
    gate.set()
    await second
    print("  and returns once it exits; detached runs left:", len(g._stopping))


async def main():
    await self_stop()
    await cancelled_stop_then_stop()

loop_errors = []
asyncio.run(main())
gc.collect()
