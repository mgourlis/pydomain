import asyncio, logging, types
from typing import ClassVar
from common import OrderPlaced, LineItemAdded, OrderShipped
from pydomain.ddd import DomainEvent
from pydomain.es.event_stream import EventStream
from pydomain.es.projection import EventSourcedProjection
from pydomain.infrastructure.subscription import Subscription, SubscriptionRunner
from pydomain.testing.fake_event_store import FakeEventStore
from pydomain.testing.fake_checkpoint_store import FakeCheckpointStore
logging.disable(logging.CRITICAL)


class Counter(EventSourcedProjection):
    name: ClassVar[str] = "counter"; version: ClassVar[int] = 1
    def __init__(self):
        super().__init__(); self.seen: list[str] = []
    async def _when_OrderPlaced(self, e): self.seen.append(e.customer_name)
    async def _when_LineItemAdded(self, e): self.seen.append(e.item_name)


class Runner(SubscriptionRunner):
    def __init__(self, *a, fail_on=None, **kw):
        super().__init__(*a, **kw); self.fail_on = fail_on; self.calls = 0
    async def process_batch(self, events, sub):
        self.calls += 1
        for e in events:
            if self.fail_on and getattr(e, "item_name", None) == self.fail_on:
                raise RuntimeError("poison")
            await sub.projection.apply(e)


async def fill(store, n, prefix="o"):
    for i in range(n):
        await store.append_to_stream(f"{prefix}{i}", [OrderPlaced(order_id=f"{prefix}{i}", customer_name=f"c{i}")], 0)


class PagingStore(FakeEventStore):
    async def read_all(self, from_version=0):
        s = await super().read_all(from_version)
        return EventStream(events=list(s.events)[:2], version=s.version)   # page of 2, total count


class LateCommitStore(FakeEventStore):
    """Positions allocated in order, but one transaction commits late (sequence-style)."""
    def __init__(self):
        super().__init__(); self.hidden = None
    async def read_all(self, from_version=0):
        visible = [e for e in self._global_log if e is not self.hidden]
        return EventStream(events=visible[from_version:], version=len(visible))


async def es17():
    store = PagingStore(); await fill(store, 5); cps = FakeCheckpointStore(); proj = Counter()
    r = Runner(store, cps); r.add_subscription(Subscription("s", proj, (OrderPlaced,)))
    await r.run_once(); await r.run_once()
    print("ES-17 paginating read_all: processed", proj.seen, "checkpoint", await cps.load("s"), "of 5")
    store2 = FakeEventStore(); await fill(store2, 5)
    s = await store2.read_all(2)
    print("ES-17 fake invariant start+len==version:", 2 + len(s.events) == s.version)


async def es18():
    store = LateCommitStore(); cps = FakeCheckpointStore(); proj = Counter()
    await fill(store, 2)
    await store.append_to_stream("late", [OrderPlaced(order_id="late", customer_name="LATE")], 0)
    store.hidden = store._global_log[-1]       # position 3 allocated, not yet committed
    # Rearrange: move the late event before a later committed one
    await store.append_to_stream("after", [OrderPlaced(order_id="after", customer_name="after")], 0)
    late = store._global_log.pop(2); store._global_log.insert(2, late)  # order: c0,c1,LATE,after
    r = Runner(store, cps); r.add_subscription(Subscription("s", proj, (OrderPlaced,)))
    await r.run_once()
    print("ES-18 before late commit: seen", proj.seen, "checkpoint", await cps.load("s"))
    store.hidden = None                         # late transaction commits
    await r.run_once()
    print("ES-18 after late commit: seen", proj.seen, "-> LATE lost, 'after' delivered twice")


async def es19():
    store = FakeEventStore(); cps = FakeCheckpointStore()
    await store.append_to_stream("p", [LineItemAdded(order_id="p", item_name="poison", price=1)], 0)
    await fill(store, 2)
    bad, good = Counter(), Counter()
    r = Runner(store, cps, fail_on="poison", failure_backoff_seconds=0)
    r.add_subscription(Subscription("bad", bad, (LineItemAdded, OrderPlaced)))
    r.add_subscription(Subscription("good", good, (OrderPlaced,)))
    for _ in range(50): await r.run_once()
    print("ES-19 after 50 cycles: bad checkpoint", await cps.load("bad"), "process_batch calls", r.calls,
          "| good checkpoint", await cps.load("good"), "good seen", good.seen)


async def es20():
    store = FakeEventStore(); cps = FakeCheckpointStore()
    await store.append_to_stream("q", [LineItemAdded(order_id="q", item_name="ok1", price=1),
                                       LineItemAdded(order_id="q", item_name="boom", price=1)], 0)
    proj = Counter(); r = Runner(store, cps, fail_on="boom", failure_backoff_seconds=0)
    r.add_subscription(Subscription("s", proj, (LineItemAdded,)))
    await r.run_once(); await r.run_once(); await r.run_once()
    print("ES-20 partial batch redelivered: seen", proj.seen, "checkpoint", await cps.load("s"))


async def es21():
    store = FakeEventStore(); cps = FakeCheckpointStore(); await fill(store, 3)
    p1, p2 = Counter(), Counter()
    class Slow(Runner):
        async def process_batch(self, events, sub):
            await asyncio.sleep(0.01); await super().process_batch(events, sub)
    r1, r2 = Slow(store, cps), Slow(store, cps)
    r1.add_subscription(Subscription("s", p1, (OrderPlaced,))); r2.add_subscription(Subscription("s", p2, (OrderPlaced,)))
    await asyncio.gather(r1.run_once(), r2.run_once())
    print("ES-21 two runners, same subscription id: runner1 seen", p1.seen, "runner2 seen", p2.seen)


async def es22():
    store = FakeEventStore(); cps = FakeCheckpointStore()
    await store.append_to_stream("a", [OrderPlaced(order_id="a", customer_name="x"),
                                       OrderShipped(order_id="a"),
                                       LineItemAdded(order_id="a", item_name="i", price=1)], 0)
    proj = Counter(); r = Runner(store, cps); r.add_subscription(Subscription("s", proj, (OrderPlaced,)))
    await r.run_once()
    print("ES-22 projection.checkpoint", proj.checkpoint, "vs CheckpointStore", await cps.load("s"))
    # documented hand loop (track-checkpoints.md §5) across a restart
    store2 = FakeEventStore(); cps2 = FakeCheckpointStore(); await fill(store2, 4)
    applied = []
    async def cycle(proj):
        cp = await cps2.load("h"); s = await store2.read_all(from_version=cp)
        for e in s.events: await proj.apply(e); applied.append(e.customer_name)
        if s.events: await cps2.save("h", proj.checkpoint)
    await cycle(Counter())                       # 4 events, checkpoint 4
    await fill(store2, 1, prefix="n")            # 5th event
    fresh = Counter()                            # process restart
    await cycle(fresh); print("ES-22 doc loop after restart: saved checkpoint", await cps2.load("h"), "(log has 5)")
    await cycle(fresh); print("ES-22 doc loop next cycle re-applies:", applied[4:])
    # rebuild doesn't reset subclass state
    p = Counter(); await p.rebuild([OrderPlaced(order_id="z", customer_name="r")]); await p.rebuild([OrderPlaced(order_id="z", customer_name="r")])
    print("ES-22 rebuild twice -> seen", p.seen)


async def es23():
    mod_a = types.SimpleNamespace(); mod_b = types.SimpleNamespace()
    class Shipped(DomainEvent):     # 'sales' context
        order_id: str
    SalesShipped = Shipped
    class Shipped(DomainEvent):     # 'warehouse' context, same __name__
        crate: str
    class P(EventSourcedProjection):
        name: ClassVar[str] = "p"; version: ClassVar[int] = 1
        async def _when_Shipped(self, e): print("ES-23 handler got", type(e).__module__, type(e).__qualname__, "fields", list(e.model_fields)[-1])
    p = P()
    try: await p.apply(Shipped(crate="c1"))
    except Exception as e: print("ES-23 collision:", type(e).__name__, e)
    class S(EventSourcedProjection):
        name: ClassVar[str] = "s"; version: ClassVar[int] = 1
        def __init__(self): super().__init__(); self.n = 0
        def _when_OrderPlaced(self, e): self.n += 1
    s = S()
    try: await s.apply(OrderPlaced(order_id="o", customer_name="c"))
    except TypeError as e: print("ES-23 sync handler:", e, "| side effect applied n =", s.n, "checkpoint", s.checkpoint)


async def main():
    for f in (es17, es18, es19, es20, es21, es22, es23):
        try: await f()
        except Exception as e: print(f.__name__, "UNEXPECTED", type(e).__name__, e)

asyncio.run(main())
