"""Probes: two relays, stale claims mid-publish, mark-after-publish failure, stop/start races."""
import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry, OutboxEntry
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.message_broker import OutboundEventGateway
from pydomain.testing import FakeOutboxStore

TOPIC = "t.synthetic"


class D(DomainEvent):
    ref: str


class I(IntegrationEvent):
    ref: str


def reg():
    r = OutboundEventRegistry()
    r.register_translation(D, I, TOPIC, lambda e: I(ref="x"))
    return r


class Clock:
    def __init__(self):
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self):
        return self.now


def entries(n):
    out = []
    for _ in range(n):
        ev = I(ref="synthetic")
        out.append(OutboxEntry(message_id=ev.event_id, topic=TOPIC, payload=ev.model_dump(), occurred_at=datetime.now(UTC)))
    return out


class Broker:
    def __init__(self, delay=0.0, on_publish=None, fail_ids=()):
        self.accepted = Counter()
        self.delay = delay
        self.on_publish = on_publish
        self.fail_ids = set(fail_ids)
        self.kwargs = []

    async def publish(self, topic, event, **kw: Any):
        self.kwargs.append(kw)
        if self.on_publish:
            self.on_publish(event)
        await asyncio.sleep(self.delay)
        if event.event_id in self.fail_ids:
            raise RuntimeError("boom")
        self.accepted[event.event_id] += 1

    async def start(self): ...
    async def stop(self): ...


async def two_run_loops():
    store = FakeOutboxStore()
    await store.append(entries(50))
    broker = Broker(delay=0.001)
    g1 = OutboundEventGateway(store, broker, reg(), batch_size=7, poll_interval_seconds=0.001)
    g2 = OutboundEventGateway(store, broker, reg(), batch_size=5, poll_interval_seconds=0.001)
    await g1.start(); await g2.start()
    for _ in range(400):
        if len(store._published_ids) == 50:
            break
        await asyncio.sleep(0.005)
    await g1.stop(); await g2.stop()
    dup = [k for k, v in broker.accepted.items() if v > 1]
    print("two run loops: published", len(set(store.published)), "dups", len(dup), "published-list dups", len(store.published) - len(set(store.published)))


async def stale_claim_mid_publish():
    """Relay A's publish outlasts the claim; relay B republishes; then A fails and exhausts."""
    clock = Clock()
    store = FakeOutboxStore(clock=clock, claim_timeout=timedelta(seconds=1))
    (e,) = entries(1)
    await store.append([e])
    gate = asyncio.Event()
    release = asyncio.Event()

    class SlowFailingBroker(Broker):
        async def publish(self, topic, event, **kw):
            gate.set()
            await release.wait()
            raise RuntimeError("late failure")

    slow = SlowFailingBroker()
    fast = Broker()
    a = OutboundEventGateway(store, slow, reg(), max_attempts=1, clock=clock)
    b = OutboundEventGateway(store, fast, reg(), clock=clock)
    ta = asyncio.create_task(a.run_once())
    await gate.wait()
    clock.now += timedelta(seconds=2)   # A's claim is stale
    print("B published:", await b.run_once())
    release.set()
    await ta
    print("stale claim: published?", store.is_published(e.message_id), "dead-lettered?", store.is_dead_lettered(e.message_id),
          "attempts", store.attempts.get(e.message_id))


async def stale_claim_both_succeed():
    clock = Clock()
    store = FakeOutboxStore(clock=clock, claim_timeout=timedelta(seconds=1))
    (e,) = entries(1)
    await store.append([e])
    gate = asyncio.Event(); release = asyncio.Event()
    accepted = Counter()

    class Slow(Broker):
        async def publish(self, topic, event, **kw):
            gate.set(); await release.wait(); accepted[event.event_id] += 1

    a = OutboundEventGateway(store, Slow(), reg(), clock=clock)
    fast = Broker()
    b = OutboundEventGateway(store, fast, reg(), clock=clock)
    ta = asyncio.create_task(a.run_once())
    await gate.wait(); clock.now += timedelta(seconds=2)
    await b.run_once(); release.set(); await ta
    print("stale claim both succeed: broker deliveries", accepted[e.message_id] + fast.accepted[e.message_id], "published list", store.published.count(e.message_id))


async def mark_published_fails():
    clock = Clock()
    store = FakeOutboxStore(clock=clock)
    es = entries(3)
    await store.append(es)
    orig = store.mark_published
    calls = {"n": 0}

    async def flaky(ids):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ConnectionError("store down")
        await orig(ids)

    store.mark_published = flaky
    broker = Broker()
    g = OutboundEventGateway(store, broker, reg(), clock=clock)
    try:
        await g.run_once()
    except ConnectionError:
        print("mark failure: pass ended with ConnectionError")
    print("  after crash: accepted", dict(Counter(broker.accepted.values())), "published", len(store.published))
    print("  immediate refetch returns", len(await store.fetch_unpublished()), "(claimed until timeout)")
    clock.now += timedelta(minutes=5)
    await g.run_once()
    print("  after timeout: accepted per entry", sorted(broker.accepted[x.message_id] for x in es))


async def stop_start_races():
    store = FakeOutboxStore()
    broker = Broker()
    g = OutboundEventGateway(store, broker, reg(), poll_interval_seconds=0.01)
    # start; stop; start; stop all without yielding
    await g.start(); await g.stop(); await g.start(); await g.stop()
    print("start/stop/start/stop: task", g._task)
    # concurrent stops
    await g.start(); await asyncio.sleep(0.02)
    await asyncio.gather(g.stop(), g.stop())
    print("concurrent stops ok, task", g._task)
    # stop concurrent with start
    await g.start()
    await asyncio.gather(g.stop(), g.start())
    print("stop||start: task", g._task, "running", g._running)
    if g._task: await g.stop()
    # direct run() as user task, stop before it begins
    t = asyncio.create_task(g.run())
    await g.stop()
    await asyncio.sleep(0.05)
    print("direct run(), stop before begin: still running?", not t.done())
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass
    # stop() raced with start() after stop already awaiting
    await g.start(); await asyncio.sleep(0.01)
    s = asyncio.create_task(g.stop())
    await asyncio.sleep(0)
    await g.start()  # no-op? task still set
    await s
    print("start during stop: task after", g._task, "-> relay running?", g._running)


async def main():
    await two_run_loops()
    await stale_claim_mid_publish()
    await stale_claim_both_succeed()
    await mark_published_fails()
    await stop_start_races()

asyncio.run(main())
