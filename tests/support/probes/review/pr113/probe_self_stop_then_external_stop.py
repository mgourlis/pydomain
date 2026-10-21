"""Probe: stop() from the relay's own publish, then an external stop() while that pass is still in flight.

The external stop() must wait until the run has exited (the finding-A guarantee).
Also: the self-stop returns and the run exits after its pass, for a start() relay and for a direct run().
"""
import asyncio
from datetime import UTC, datetime

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry, OutboxEntry
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.message_broker import OutboundEventGateway
from pydomain.testing import FakeOutboxStore


class D(DomainEvent): ...
class I(IntegrationEvent): ...


def entry():
    ev = I()
    return OutboxEntry(message_id=ev.event_id, topic="t", payload=ev.model_dump(), occurred_at=datetime.now(UTC))


class Broker:
    def __init__(self):
        self.g = None
        self.self_stopped = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def publish(self, topic, event, **kw):
        self.calls += 1
        if self.calls == 1:
            await self.g.stop()          # returns at once now
            self.self_stopped.set()
            await self.release.wait()    # the pass is still in flight
    async def start(self): ...
    async def stop(self): ...


async def scenario(direct: bool):
    b = Broker()
    store = FakeOutboxStore()
    r = OutboundEventRegistry()
    r.register_translation(D, I, "t", lambda e: I())
    g = OutboundEventGateway(store, b, r, poll_interval_seconds=0.005)
    b.g = g
    await store.append([entry(), entry()])
    if direct:
        task = asyncio.create_task(g.run())
    else:
        await g.start()
        task = g._task
    await asyncio.wait_for(b.self_stopped.wait(), 2)
    ext = asyncio.create_task(g.stop())
    await asyncio.sleep(0.05)
    early = ext.done()
    b.release.set()
    await asyncio.wait_for(task, 2)
    await ext
    print(f"{'direct run()' if direct else 'start()'}: self-stop returned; run exited after its pass:",
          task.done(), "| published", len(store.published),
          "| external stop() returned while the pass was in flight:", early)


async def main():
    await scenario(direct=False)
    await scenario(direct=True)

asyncio.run(main())
