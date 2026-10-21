"""Probe: stop() after the relay task died (B-65 gap) and whether start() restarts it."""
import asyncio, gc, sys
from pydomain.cqrs.outbox import OutboundEventRegistry
from pydomain.infrastructure.message_broker import OutboundEventGateway
from pydomain.testing import FakeOutboxStore


class Boom(BaseException):
    pass


class Store(FakeOutboxStore):
    calls = 0
    async def fetch_unpublished(self, limit=100):
        Store.calls += 1
        if Store.calls == 1:
            raise Boom()
        return await super().fetch_unpublished(limit)


class B:
    async def publish(self, topic, event, **kw): ...


async def main():
    loop = asyncio.get_running_loop()
    seen = []
    loop.set_exception_handler(lambda l, c: seen.append(c.get("message")))
    g = OutboundEventGateway(Store(), B(), OutboundEventRegistry(), poll_interval_seconds=0.01)
    await g.start(); await asyncio.sleep(0.05)
    print("task done:", g._task.done(), "exc:", type(g._task.exception()).__name__ if g._task.done() and not g._task.cancelled() else None)
    await g.start(); print("start() after death restarted?", not g._task.done())
    await g.stop(); print("stop() returned without raising")
    await g.start(); await asyncio.sleep(0.03); print("start() after stop restarted?", g._task is not None and not g._task.done(), "fetches", Store.calls)
    await g.stop()
    gc.collect(); await asyncio.sleep(0); print("loop exception handler saw:", seen)

try:
    asyncio.run(main())
except Boom:
    print("Boom escaped asyncio.run")
