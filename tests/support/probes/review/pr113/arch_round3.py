"""Probe round 3: collision wording (first/also binders), concurrent stops wait, start-then-direct-run stop hang (known gap)."""
import asyncio
from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.message_broker import OutboundEventGateway
from pydomain.testing import FakeOutboxStore

class X(IntegrationEvent): pass
class Y(IntegrationEvent): pass
class A(DomainEvent): pass
class B(DomainEvent): pass
class C(DomainEvent): pass
r = OutboundEventRegistry()
for d in (A, B, C): r.register_translation(d, X, "t1", lambda e: None)
for d in (A, B): 
    try: r.register_translation(d, Y, "t1", lambda e: None)
    except ValueError as e: print("rebind", d.__name__, "->", e)
r2 = OutboundEventRegistry(); r2.register_translation(A, X, "t", lambda e: None); r2.register_translation(A, Y, "t", lambda e: None)
print("single-binder hot swap ok:", r2.resolve("t").__name__)

class Slow:
    async def publish(self, *a, **k): await asyncio.sleep(0.05)

async def main():
    g = OutboundEventGateway(FakeOutboxStore(), Slow(), OutboundEventRegistry(), poll_interval_seconds=0.05)
    await g.start(); await asyncio.sleep(0.01)
    s1 = asyncio.create_task(g.stop()); s2 = asyncio.create_task(g.stop())
    await asyncio.wait_for(asyncio.gather(s1, s2), 1); print("two concurrent stops returned; running:", g._task)
    g = OutboundEventGateway(FakeOutboxStore(), Slow(), OutboundEventRegistry(), poll_interval_seconds=0.01)
    await g.start(); direct = asyncio.create_task(g.run()); await asyncio.sleep(0.03)
    try:
        await asyncio.wait_for(g.stop(), 0.3); print("start+direct run: stop returned (gap closed?)")
    except TimeoutError:
        print("start+direct run: stop() hangs (documented known gap)")
    for t in asyncio.all_tasks() - {asyncio.current_task()}: t.cancel()
    await asyncio.sleep(0)

asyncio.run(main())
