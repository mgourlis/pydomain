import asyncio, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "es"))
from common import Order, LineItemAdded
from pydomain.es.event_sourced_repository import EventSourcedRepository
from pydomain.es.snapshot import SnapshotThresholdPolicy
from pydomain.testing import FakeEventStore, FakeSnapshotStore

async def main():
    store = FakeEventStore(); snaps = FakeSnapshotStore()
    repo = EventSourcedRepository(store, Order, snapshot_store=snaps, snapshot_policy=SnapshotThresholdPolicy(2))
    o = Order(id="o6"); o.place("Synthetic"); o.add("a", 1.0)
    await repo.save(o)
    sn = await snaps.get(repo.aggregate_type, "o6")
    print("snapshot version", sn.version, "items", sn.state.get("items"), "stream len", len((await store.read_stream("o6")).events))
    await store.append_to_stream("o6", [LineItemAdded(order_id="o6", item_name="b", price=1.0)], 2)
    for i in range(3):
        l = await repo.get_by_id("o6")
        print(f"load {i+1}: version {l.version} items {[x['name'] for x in l.items]}; stored snapshot items now {[x['name'] for x in sn.state['items']]}")
    # control: without snapshot store
    repo2 = EventSourcedRepository(store, Order)
    print("control full replay:", [x['name'] for x in (await repo2.get_by_id("o6")).items])
asyncio.run(main())
