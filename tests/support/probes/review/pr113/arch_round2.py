"""Probe round 2: B-70 depth/cycle, B-71/B-79 marks, B-72 start during stop, B-73 message, arg types, attempt overlap."""
import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry, OutboxEntry
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.message_broker import OutboundEventGateway
from pydomain.testing import FakeOutboxStore


class E(IntegrationEvent):
    data: Any = None


def msg(**kw):
    try:
        E(**kw); return "ACCEPTED"
    except ValidationError as e:
        return e.errors()[0]["msg"].split(". IntegrationEvent")[0][:120]
    except RecursionError:
        return "RecursionError"

def nest(n):
    v = 1
    for _ in range(n): v = [v]
    return v
print("B-70 depth 100:", msg(data=nest(100)))
print("B-70 depth 101:", msg(data=nest(101)))
print("B-70 depth 100000:", msg(data=nest(100000)))
c = {}; c["self"] = c
print("B-70 cycle:", msg(data=c))
shared = [1]
print("B-70 shared (DAG, not cycle):", msg(data={"a": shared, "b": shared}))

now = [datetime(2026, 1, 1, tzinfo=UTC)]
clock = lambda: now[0]

class D(DomainEvent):
    pass

class Weird(IntegrationEvent):
    n: int
    @classmethod
    def model_validate(cls, obj, **kw):
        raise RuntimeError("boom")

class Rec:
    def __init__(self): self.n = 0
    async def publish(self, topic, event, **kw): self.n += 1
    async def start(self): ...
    async def stop(self): ...

async def main():
    # any exception from model_validate is a delivery failure
    reg = OutboundEventRegistry(); reg.register_translation(D, Weird, "w", lambda e: None)
    s = FakeOutboxStore(clock=clock)
    await s.append([OutboxEntry(message_id=str(uuid4()), topic="w", payload={"n": 1}, occurred_at=clock())])
    print("B-70 relay non-ValidationError:", await OutboundEventGateway(s, Rec(), reg, clock=clock).run_once(), s.failures[0][1] if s.failures else "no failure")

    # B-71 / B-79
    s = FakeOutboxStore(clock=clock); mid = str(uuid4())
    await s.append([OutboxEntry(message_id=mid, topic="t", payload={}, occurred_at=clock())])
    await s.mark_published([mid]); await s.mark_failed(mid, error="late", next_attempt_at=clock()); await s.mark_dead_lettered(mid, error="late")
    print("B-71 published wins:", s.is_published(mid), "dead:", mid in s.dead_lettered, "attempts:", s.attempts.get(mid))
    s = FakeOutboxStore(clock=clock); mid = str(uuid4())
    await s.append([OutboxEntry(message_id=mid, topic="t", payload={}, occurred_at=clock())])
    await s.mark_dead_lettered(mid, error="x"); await s.mark_published([mid])
    print("B-79 after dead letter:", s.is_published(mid), "dead:", mid in s.dead_lettered, "fetched:", len(await s.fetch_unpublished(10)))

    # attempts overlap: two relays, stale claim, both fail from same count
    class Fail:
        async def publish(self, *a, **k): raise ConnectionError()
    reg2 = OutboundEventRegistry()
    class I(IntegrationEvent): pass
    reg2.register_translation(D, I, "t", lambda e: I())
    s = FakeOutboxStore(clock=clock, claim_timeout=timedelta(seconds=1)); mid = str(uuid4())
    await s.append([OutboxEntry(message_id=mid, topic="t", payload=I().model_dump(), occurred_at=clock())])
    g = OutboundEventGateway(s, Fail(), reg2, clock=clock, max_attempts=2, base_delay=timedelta(0), max_delay=timedelta(0))
    # relay A fetches, stalls; claim stale; relay B fetches same entry; both fail
    a = await s.fetch_unpublished(10); now[0] += timedelta(seconds=2); b = await s.fetch_unpublished(10)
    for e in a + b:
        att = e.attempts + 1
        if att >= 2: await s.mark_dead_lettered(e.message_id, error="x")
        else: await s.mark_failed(e.message_id, error="x", next_attempt_at=clock())
    print("overlap after 2 stale overlapping relays (max 2):", s.attempts[mid], "dead:", mid in s.dead_lettered)

    # B-72 start during stop
    class Slow:
        async def publish(self, *a, **k): await asyncio.sleep(0.1)
    s = FakeOutboxStore(); await s.append([OutboxEntry(message_id=str(uuid4()), topic="t", payload=I().model_dump(), occurred_at=datetime.now(UTC))])
    g = OutboundEventGateway(s, Slow(), reg2, poll_interval_seconds=0.01)
    await g.start(); await asyncio.sleep(0.02)
    stopping = asyncio.create_task(g.stop()); await asyncio.sleep(0)
    await g.start(); await stopping
    t = g._task
    print("B-72 fresh relay after start during stop:", t is not None and not t.done())
    await g.stop()

    # B-73 message
    class A1(IntegrationEvent): pass
    class B1(IntegrationEvent): pass
    class DA(DomainEvent): pass
    class DB(DomainEvent): pass
    r = OutboundEventRegistry(); r.register_translation(DA, A1, "t1", lambda e: None); r.register_translation(DA, A1, "t2", lambda e: None)
    try: r.register_translation(DB, B1, "t1", lambda e: None)
    except ValueError as e: print("B-73:", e)

    # arg types
    for kw in (dict(batch_size=True), dict(batch_size=1.0), dict(max_attempts=False), dict(poll_interval_seconds=float("nan")), dict(failure_backoff_seconds=float("inf")), dict(max_delay=timedelta(seconds=-1), base_delay=timedelta(seconds=-2))):
        try: OutboundEventGateway(FakeOutboxStore(), Rec(), reg2, **kw); print("args", kw, "ACCEPTED")
        except ValueError as e: print("args", kw, "->", e)
    for lim in (True, 1.0):
        try: await FakeOutboxStore().fetch_unpublished(lim); print("limit", lim, "ACCEPTED")
        except ValueError as e: print("limit", lim, "->", e)

asyncio.run(main())
