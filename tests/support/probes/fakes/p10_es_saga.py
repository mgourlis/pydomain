"""FAKE-10 (ES-03/04/05/09) and NEW-F-2 probes. Synthetic."""
import asyncio, sys
from uuid import uuid4
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "es"))
from common import Order, OrderPlaced, LineItemAdded
from pydomain.ddd.exceptions import ConcurrencyError
from pydomain.es.exceptions import DuplicateCommandError
from pydomain.es.event_sourced_repository import EventSourcedRepository
from pydomain.es.snapshot import Snapshot, SnapshotThresholdPolicy
from pydomain.testing import FakeEventStore, FakeSnapshotStore
from pydomain.testing.fake_saga_repository import FakeSagaRepository
from pydomain.cqrs.saga.state import SagaState, SagaStatus

def ev(i): return LineItemAdded(order_id="o1", item_name=f"i{i}", price=1.0)

async def es03():
    s = FakeEventStore(); cid = uuid4()
    await s.append_to_stream("o1", [ev(0)], 0)
    try: await s.append_to_stream("o1", [ev(1)], 0, command_id=cid)
    except ConcurrencyError: print("ES-03 first try: ConcurrencyError (stale version)")
    try:
        await s.append_to_stream("o1", [ev(1)], 1, command_id=cid)
        print("ES-03 retry with correct version: OK")
    except DuplicateCommandError as e: print("ES-03 retry with correct version: DuplicateCommandError", e)
    print("ES-03 stream len:", len((await s.read_stream("o1")).events))

async def es04():
    s = FakeEventStore()
    await s.append_to_stream("o2", [], 0)
    st = await s.read_stream("o2"); print("ES-04 empty append created stream: version", st.version, "events", len(st.events))
    await s.append_to_stream("o2", [ev(0)], 0); print("ES-04 second expected-0 append on 'existing' stream: accepted")
    repo = None
    s2 = FakeEventStore(); await s2.append_to_stream("o3", [], 0)
    try:
        repo2 = EventSourcedRepository(s2, Order)
        agg = await repo2.get_by_id("o3")
        print("ES-04 get_by_id on empty stream ->", None if agg is None else f"Order(version={agg.version})")
    except Exception as e: print("ES-04 repo ctor/get:", type(e).__name__, e)

async def es05():
    s = FakeEventStore()
    await s.append_to_stream("o4", [ev(i) for i in range(5)], 0)
    r = await s.read_stream("o4", -2); print("ES-05 read_stream(from=-2):", [e.item_name for e in r.events])
    a = await s.read_all(-1); print("ES-05 read_all(from=-1):", [e.item_name for e in a.events])

async def es09():
    snaps = FakeSnapshotStore()
    snap = Snapshot(aggregate_id="o5", version=1, state={"items": []})
    await snaps.save("Order", snap)
    got = await snaps.get("Order", "o5")
    print("ES-09 get returns same object:", got is snap)
    got.state["items"].append("mutated"); print("ES-09 stored snapshot after caller mutation:", (await snaps.get("Order","o5")).state)
    # through repository
    store = FakeEventStore(); snaps = FakeSnapshotStore()
    repo = EventSourcedRepository(store, Order, snapshot_store=snaps, snapshot_policy=SnapshotThresholdPolicy(2))
    o = Order(id="o6"); o.place("Synthetic"); o.add("a", 1.0)
    await repo.save(o)
    print("ES-09 snapshot saved:", (await snaps.get(repo.aggregate_type, "o6")) is not None)
    await store.append_to_stream("o6", [LineItemAdded(order_id="o6", item_name="b", price=1.0)], 2)
    l1 = await repo.get_by_id("o6"); l2 = await repo.get_by_id("o6"); l3 = await repo.get_by_id("o6")
    print("ES-09 items per load (expect 2 each):", len(l1.items), len(l2.items), len(l3.items))

async def newf2():
    r = FakeSagaRepository()
    s = SagaState(saga_type="SyntheticSaga", status=SagaStatus.COMPENSATING, correlation_id=uuid4())
    await r.save(s)
    st = await r.find_stalled_sagas()
    print("NEW-F-2 COMPENSATING saga with pending_commands=", s.pending_commands, "returned by find_stalled_sagas:", [x.status.value for x in st])

for f in (es03, es04, es05, es09, newf2):
    try: asyncio.run(f())
    except Exception as e: print(f.__name__, "ERROR", type(e).__name__, e)
