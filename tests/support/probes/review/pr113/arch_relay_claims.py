"""Probe: claim/lease, store error leaves undelivered entries claimed, headers, stamping, arguments."""
import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry, OutboxEntry
from pydomain.infrastructure.message_broker import OutboundEventGateway
from pydomain.testing import FakeOutboxStore, InMemoryMessageBroker


class Shipped(IntegrationEvent):
    ref: str


now = [datetime(2026, 1, 1, tzinfo=UTC)]
clock = lambda: now[0]


def entry(i, corr=None, cause=None):
    ev = Shipped(ref=f"R{i}")
    return OutboxEntry(message_id=ev.event_id, topic="t", payload=ev.model_dump(),
                       occurred_at=clock(), correlation_id=corr, causation_id=cause)


class Rec:
    def __init__(self): self.calls = []
    async def publish(self, topic, event, **kw): self.calls.append((topic, event, kw))
    async def start(self): ...
    async def stop(self): ...


class NoKw:
    async def publish(self, topic, event): ...
    async def start(self): ...
    async def stop(self): ...


async def main():
    reg = OutboundEventRegistry()
    reg.register_translation(type("D", (), {}), Shipped, "t", lambda e: None) if False else None
    # registry: resolve needs a registration; use a dummy DomainEvent subclass
    from pydomain.ddd.domain_event import DomainEvent
    class D(DomainEvent):
        x: int = 0
    reg.register_translation(D, Shipped, "t", lambda e: Shipped(ref="x"))

    # 1. claim/lease and default
    s = FakeOutboxStore(clock=clock)
    await s.append([entry(i) for i in range(3)])
    a = await s.fetch_unpublished(2); b = await s.fetch_unpublished(10)
    print("1 claim: first", len(a), "second", len(b), "disjoint", not {e.message_id for e in a} & {e.message_id for e in b})
    print("1 third fetch (all claimed):", len(await s.fetch_unpublished(10)))
    now[0] += timedelta(minutes=5) - timedelta(seconds=1)
    print("1 at 4:59:", len(await s.fetch_unpublished(10)))
    now[0] += timedelta(seconds=1)
    print("1 at exactly 5:00 (stale?):", len(await s.fetch_unpublished(10)))
    for bad in (timedelta(0), timedelta(seconds=-1)):
        try: FakeOutboxStore(claim_timeout=bad); print("1 claim_timeout", bad, "accepted!")
        except ValueError as e: print("1 claim_timeout", bad, "->", e)
    for lim in (0, -1):
        try: await s.fetch_unpublished(lim); print("limit", lim, "accepted!")
        except ValueError as e: print("1 limit", lim, "->", e)
    # mark_failed releases claim; entry waits for next_attempt_at
    s2 = FakeOutboxStore(clock=clock); e0 = entry(9); await s2.append([e0])
    await s2.fetch_unpublished(1)
    await s2.mark_failed(e0.message_id, error="x", next_attempt_at=clock())
    print("1 after mark_failed(next_attempt_at=now):", len(await s2.fetch_unpublished(1)))

    # 2. store error mid-batch: undelivered entries stay claimed until stale
    s3 = FakeOutboxStore(clock=clock)
    es = [entry(i) for i in range(3)]; await s3.append(es)
    broker = Rec()
    orig = s3.mark_published
    n = [0]
    async def flaky(ids):
        n[0] += 1
        if n[0] == 1: raise RuntimeError("store down")
        await orig(ids)
    s3.mark_published = flaky
    g = OutboundEventGateway(s3, broker, reg, clock=clock)
    try: await g.run_once()
    except RuntimeError as e: print("2 run_once propagated:", e)
    print("2 published after error:", len(broker.calls), "marked:", s3.published)
    print("2 refetch right away:", len(await s3.fetch_unpublished(10)))
    now[0] += timedelta(minutes=5)
    got = await s3.fetch_unpublished(10)
    print("2 refetch after stale:", len(got), "(includes the published-but-unmarked one:", es[0].message_id in {x.message_id for x in got}, ")")

    # 3. headers and stamping
    s4 = FakeOutboxStore(clock=clock); broker = Rec()
    c, k = str(uuid4()), str(uuid4())
    await s4.append([entry(1, c, k), entry(2), entry(3, c, None)])
    g = OutboundEventGateway(s4, broker, reg, clock=clock)
    await g.run_once()
    for t, ev, kw in broker.calls:
        print("3 kw", kw, "ids", ev.correlation_id, ev.causation_id)
    # entry wins over translator-set ids in payload
    s5 = FakeOutboxStore(clock=clock); broker = Rec()
    ev = Shipped(ref="p").stamp(correlation_id="payload-c", causation_id="payload-k")
    await s5.append([OutboxEntry(message_id=ev.event_id, topic="t", payload=ev.model_dump(), occurred_at=clock(), correlation_id=c, causation_id=k)])
    await OutboundEventGateway(s5, broker, reg, clock=clock).run_once()
    print("3 entry wins:", broker.calls[0][1].correlation_id == c)
    # unstamped entry but payload has translator ids: is it 'published unstamped'?
    s6 = FakeOutboxStore(clock=clock); broker = Rec()
    await s6.append([OutboxEntry(message_id=ev.event_id, topic="t", payload=ev.model_dump(), occurred_at=clock())])
    await OutboundEventGateway(s6, broker, reg, clock=clock).run_once()
    print("3 entry without ids, payload ids kept:", broker.calls[0][1].correlation_id, broker.calls[0][2])
    # broker without **kwargs: stamped delivery fails, unstamped succeeds
    s7 = FakeOutboxStore(clock=clock); await s7.append([entry(1, c, k), entry(2)])
    n_ok = await OutboundEventGateway(s7, NoKw(), reg, clock=clock).run_once()
    print("3 no-kwargs broker: published", n_ok, "failures", [f[1] for f in s7.failures])
    # InMemoryMessageBroker accepts headers
    s8 = FakeOutboxStore(clock=clock); await s8.append([entry(1, c, k)])
    imb = InMemoryMessageBroker()
    print("3 InMemoryMessageBroker:", await OutboundEventGateway(s8, imb, reg, clock=clock).run_once())

    # 4. argument errors
    cases = dict(batch_size=0, max_attempts=0, base_delay=timedelta(seconds=-1), max_delay=timedelta(seconds=-1),
                 poll_interval_seconds=-1, failure_backoff_seconds=-0.1)
    for k_, v in cases.items():
        try: OutboundEventGateway(FakeOutboxStore(), Rec(), reg, **{k_: v}); print("4", k_, "accepted!")
        except ValueError as e: print("4", k_, "->", e)
    try: OutboundEventGateway(FakeOutboxStore(), Rec(), reg, base_delay=timedelta(seconds=10), max_delay=timedelta(seconds=5))
    except ValueError as e: print("4 max<base ->", e)
    for v in (float("nan"), float("inf")):
        try: OutboundEventGateway(FakeOutboxStore(), Rec(), reg, poll_interval_seconds=v); print("4 poll", v, "accepted")
        except ValueError as e: print("4 poll", v, "->", e)
    OutboundEventGateway(FakeOutboxStore(), Rec(), reg, max_attempts=None); print("4 max_attempts=None ok")

    # 5. stop right after start
    g = OutboundEventGateway(FakeOutboxStore(), Rec(), reg, poll_interval_seconds=0.01)
    await g.start(); await asyncio.wait_for(g.stop(), 1); print("5 stop after start ok, running:", g._running)

asyncio.run(main())
