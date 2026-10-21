import asyncio, uuid
from common import Order, OrderPlaced, LineItemAdded, Address
from pydomain.ddd.exceptions import ConcurrencyError
from pydomain.es.exceptions import DuplicateCommandError
from pydomain.es.event_sourced_repository import EventSourcedRepository
from pydomain.es.snapshot import Snapshot, SnapshotThresholdPolicy
from pydomain.testing.fake_event_store import FakeEventStore
from pydomain.testing.fake_snapshot_store import FakeSnapshotStore
from pydomain.testing.fake_unit_of_work import FakeUnitOfWork


async def es01():
    store = FakeEventStore(); repo = EventSourcedRepository(store, Order)
    a = Order(id="o1"); a.place("Alice"); await repo.save(a)
    stale = await repo.get_by_id("o1"); other = await repo.get_by_id("o1")
    other.add("widget", 1.0); await repo.save(other)
    stale.add("gadget", 2.0)
    try:
        await repo.save(stale)
    except ConcurrencyError as e:
        print("ES-01 first save:", type(e).__name__)
    print("ES-01 after error: version", stale.version, "pending", len(stale.pull_events()))
    r = await repo.save(stale)
    s = await store.read_stream("o1")
    print("ES-01 retry same instance returned", r, "; stream len", s.version, "; gadget stored?",
          any(getattr(e, "item_name", None) == "gadget" for e in s.events))


async def es02_and_leak():
    class BoomSnap(FakeSnapshotStore):
        async def save(self, t, s):
            raise RuntimeError("snapshot store down")
    store = FakeEventStore()
    repo = EventSourcedRepository(store, Order, snapshot_store=BoomSnap(),
                                  snapshot_policy=SnapshotThresholdPolicy(0))
    uow = FakeUnitOfWork(repository=repo)
    try:
        async with uow:
            a = Order(id="o2"); a.place("Bob"); await repo.save(a)
            await uow.commit()
    except RuntimeError as e:
        print("ES-02 save raised:", e)
    s = await store.read_stream("o2")
    print("ES-02 stored events:", s.version, "; uow.collect_events:", len(uow.collect_events()))
    # reuse the same repo/uow for an unrelated command
    repo._snapshot_policy = None  # stop snapshotting, only to let the 2nd command succeed
    uow._correlation_id = uuid.UUID(int=2); uow._causation_id = uuid.UUID(int=2)
    async with uow:
        b = Order(id="o3"); b.place("Carol"); await repo.save(b)
        await uow.commit()
    print("NEW-leak: 2nd commit published aggregate ids:",
          [e.order_id for e in uow.collect_events()])


async def es03():
    store = FakeEventStore(); repo = EventSourcedRepository(store, Order)
    a = Order(id="o4"); a.place("Dan"); await repo.save(a)
    cid = uuid.uuid4()
    stale = await repo.get_by_id("o4")
    other = await repo.get_by_id("o4"); other.add("x", 1); await repo.save(other)
    stale.add("y", 2)
    try:
        await repo.save(stale, command_id=cid)
    except ConcurrencyError:
        print("ES-03 first attempt: ConcurrencyError")
    fresh = await repo.get_by_id("o4"); fresh.add("y", 2)   # documented retry: reload + reapply
    try:
        await repo.save(fresh, command_id=cid)
        print("ES-03 retry ok")
    except DuplicateCommandError as e:
        print("ES-03 documented retry raised DuplicateCommandError:", e)
    print("ES-03 stream len", (await store.read_stream("o4")).version, "(y never stored)")


async def es04():
    store = FakeEventStore(); repo = EventSourcedRepository(store, Order)
    await store.append_to_stream("o5", [], 0)
    got = await repo.get_by_id("o5")
    print("ES-04 get_by_id after empty append:", None if got is None else f"aggregate v{got.version}")
    await store.append_to_stream("o5", [OrderPlaced(order_id="o5", customer_name="Eve")], 0)
    print("ES-04 second expected_version=0 append on existing stream: succeeded")
    a = Order(id="o5b"); r = await repo.save(a)
    print("ES-04 repo.save with no events is a no-op; stream created?", "o5b" in store._store)


async def es05():
    store = FakeEventStore(); snaps = FakeSnapshotStore()
    repo = EventSourcedRepository(store, Order, snapshot_store=snaps)
    a = Order(id="o6"); a.place("Fay"); a.add("a", 1); a.add("b", 2); await repo.save(a)
    s = await store.read_stream("o6", from_version=-1)
    print("ES-05 read_stream(from_version=-1) ->", len(s.events), "event(s):", type(s.events[0]).__name__)
    await snaps.save("Order", Snapshot(aggregate_id="o6", version=-2, state={"items": []}))
    got = await repo.get_by_id("o6")
    print("ES-05 snapshot v=-2 load: version", got.version, "items", got.items, "status", got.status)


async def es06():
    store = FakeEventStore(); repo = EventSourcedRepository(store, Order)
    uow = FakeUnitOfWork(repository=repo)
    uow._correlation_id = uuid.uuid4(); uow._causation_id = uuid.uuid4()
    async with uow:
        a = Order(id="o7"); a.place("Gus"); await repo.save(a); await uow.commit()
    pub = uow.collect_events()[0]; stored = (await store.read_all()).events[0]
    print("ES-06 published corr", pub.correlation_id is not None, "| stored corr", stored.correlation_id,
          "cause", stored.causation_id, "| same event_id", pub.event_id == stored.event_id)


async def es08():
    store = FakeEventStore(); snaps = FakeSnapshotStore()
    repo = EventSourcedRepository(store, Order, snapshot_store=snaps, snapshot_policy=SnapshotThresholdPolicy(0))
    a = Order(id="o8"); a.place("Hal"); await repo.save(a)
    got = await repo.get_by_id("o8")
    print("ES-08 nested field after snapshot load:", type(got.address).__name__, got.address)
    # removed field: snapshot taken by an older schema
    await snaps.save("Order", Snapshot(aggregate_id="o8", version=1, state={"legacy_field": 1}))
    try:
        await repo.get_by_id("o8")
    except Exception as e:
        print("ES-08 snapshot with removed field:", type(e).__name__, str(e).splitlines()[0])


async def es09():
    store = FakeEventStore(); snaps = FakeSnapshotStore()
    repo = EventSourcedRepository(store, Order, snapshot_store=snaps, snapshot_policy=SnapshotThresholdPolicy(2))
    a = Order(id="o9"); a.place("Ivy"); a.add("a", 1); await repo.save(a)   # v2 -> snapshot
    b = await repo.get_by_id("o9"); b.add("b", 2); await repo.save(b)       # v3, no snapshot
    l1 = await repo.get_by_id("o9"); l2 = await repo.get_by_id("o9"); l3 = await repo.get_by_id("o9")
    print("ES-09 items per load:", len(l1.items), len(l2.items), len(l3.items), "(stream has 2 items)")
    print("ES-09 stored snapshot items now:", len((await snaps.get("Order", "o9")).state["items"]))


async def es10():
    store = FakeEventStore(); snaps = FakeSnapshotStore()
    repo = EventSourcedRepository(store, Order, snapshot_store=snaps)
    a = Order(id="o10"); a.place("Jo"); await repo.save(a)
    await snaps.save("Order", Snapshot(aggregate_id="o10", version=5, state={"status": "placed"}))
    for i in range(2):
        b = await repo.get_by_id("o10"); b.add("z", 1)
        try:
            await repo.save(b)
        except ConcurrencyError as e:
            print(f"ES-10 save #{i+1}:", e)


async def es11():
    store = FakeEventStore(); snaps = FakeSnapshotStore()
    repo = EventSourcedRepository(store, Order, snapshot_store=snaps)
    await snaps.save("Order", Snapshot(aggregate_id="gone", version=3, state={"status": "placed"}))
    got = await repo.get_by_id("gone")
    print("ES-11 stream absent, snapshot present ->", None if got is None else f"aggregate v{got.version} status={got.status}")


async def es12():
    store = FakeEventStore(); snaps = FakeSnapshotStore()
    repo = EventSourcedRepository(store, Order, snapshot_store=snaps, snapshot_policy=SnapshotThresholdPolicy(10))
    a = Order(id="o12"); a.place("K")
    for i in range(8): a.add(str(i), 1)
    await repo.save(a)  # v9
    b = await repo.get_by_id("o12")
    for i in range(3): b.add("x", 1)
    await repo.save(b)  # v12, crossed 10
    print("ES-12 after 9 -> 12 with threshold 10, snapshot:", await snaps.get("Order", "o12"))
    seen = []
    class Spy(SnapshotThresholdPolicy):
        def should_snapshot(self, **kw):
            seen.append(kw["pending_event_count"]); return super().should_snapshot(**kw)
    repo2 = EventSourcedRepository(store, Order, snapshot_store=snaps, snapshot_policy=Spy(10))
    c = await repo2.get_by_id("o12"); c.add("y", 1); await repo2.save(c)
    print("ES-12 pending_event_count passed at v13 (1 event in this save, 13 since no snapshot):", seen)


async def main():
    for f in (es01, es02_and_leak, es03, es04, es05, es06, es08, es09, es10, es11, es12):
        try:
            await f()
        except Exception as e:
            print(f.__name__, "UNEXPECTED", type(e).__name__, e)

asyncio.run(main())
