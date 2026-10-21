import asyncio, warnings
from pydantic import computed_field
from common import Order, OrderPlaced, LineItemAdded, OrderShipped
from pydomain.ddd import DomainEvent
from pydomain.es.aggregate import EventSourcedAggregateRoot
from pydomain.es.event_sourced_repository import EventSourcedRepository
from pydomain.es.event_stream import EventStream
from pydomain.es.snapshot import SnapshotThresholdPolicy, RejectStaleSnapshotPolicy
from pydomain.es.upcasting import EventUpcaster, UpcasterRegistry
from pydomain.es.exceptions import UpcastError
from pydomain.infrastructure.event_registry import EventRegistry, GenericDomainEvent
from pydomain.testing.fake_event_store import FakeEventStore
from pydomain.testing.fake_snapshot_store import FakeSnapshotStore


class Ticket(EventSourcedAggregateRoot[str]):
    status: str = "new"
    def open(self): self._apply(OrderPlaced(order_id=self.id, customer_name="n"))
    def _when(self, e):
        if isinstance(e, OrderPlaced):
            self.status = "open"
            self.ship()                       # _when calls an _apply-based method
        elif isinstance(e, OrderShipped):
            self.status = "shipped"
        else:
            raise ValueError(f"Unknown event: {e!r}")
    def ship(self): self._apply(OrderShipped(order_id=self.id))


class Priced(EventSourcedAggregateRoot[str]):
    qty: int = 0
    @computed_field
    @property
    def double(self) -> int: return self.qty * 2
    def _when(self, e): self.qty += 1


async def es07():
    store = FakeEventStore(); repo = EventSourcedRepository(store, Ticket)
    t = Ticket(id="t1"); t.open(); await repo.save(t)
    print("ES-07 stored events:", (await store.read_stream("t1")).version)
    loaded = await repo.get_by_id("t1")
    print("ES-07 after load: version", loaded.version, "pending", [type(e).__name__ for e in loaded.pull_events()])
    try:
        EventStream(events=[GenericDomainEvent(type="Legacy", data={}, version=1)], version=1)
        print("ES-07 EventStream accepted GenericDomainEvent")
    except Exception as e:
        print("ES-07 EventStream rejects GenericDomainEvent:", type(e).__name__, str(e).splitlines()[0])
    print("ES-07 GenericDomainEvent is a DomainEvent?", issubclass(GenericDomainEvent, DomainEvent))


async def es08b():
    store = FakeEventStore(); snaps = FakeSnapshotStore()
    repo = EventSourcedRepository(Priced and store, Priced, snapshot_store=snaps, snapshot_policy=SnapshotThresholdPolicy(0))
    p = Priced(id="p1"); p._apply(LineItemAdded(order_id="p1", item_name="i", price=1)); await repo.save(p)
    try:
        await repo.get_by_id("p1")
        print("ES-08 computed field load ok")
    except Exception as e:
        print("ES-08 computed field in snapshot:", type(e).__name__, str(e).splitlines()[0])
    # nested dict consequence
    store2 = FakeEventStore(); snaps2 = FakeSnapshotStore()
    repo2 = EventSourcedRepository(store2, Order, snapshot_store=snaps2, snapshot_policy=SnapshotThresholdPolicy(0))
    o = Order(id="o1"); o.place("A"); await repo2.save(o)
    lo = await repo2.get_by_id("o1")
    try:
        print(lo.address.city)
    except AttributeError as e:
        print("ES-08 nested access after load:", e)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        lo.add("x", 1); await repo2.save(lo)
        print("ES-08 re-snapshot warnings:", [str(x.message).splitlines()[0][:80] for x in w])
    # with schema policy and bumped version -> fallback works
    class Order2(Order):
        _snapshot_schema_version = 2
    repo3 = EventSourcedRepository(store2, Order2, snapshot_store=snaps2, snapshot_schema_policy=RejectStaleSnapshotPolicy())
    # aggregate_type differs (Order2) so snapshot not found; demonstrates nothing; skip


def es14():
    reg = UpcasterRegistry()
    class A(EventUpcaster):
        source_type = "OrderPlaced"; source_version = 1; target_version = 2
        def _transform(self, e): return {**e, "a": 1}
    class B(EventUpcaster):
        source_type = "OrderPlaced"; source_version = 1; target_version = 2
        def _transform(self, e): return {**e, "b": 1}
    reg.register(A); reg.register(B)
    print("ES-14 duplicate register silent; resolve ->", [c.__name__ for c in reg.resolve("OrderPlaced", 1)])
    class NoTarget(EventUpcaster):
        source_type = "X"; source_version = 1
        def _transform(self, e): return e
    reg.register(NoTarget)
    try: reg.resolve("X", 1)
    except Exception as e: print("ES-14 missing target_version accepted at register; resolve:", type(e).__name__)
    class Down(EventUpcaster):
        source_type = "Y"; source_version = 3; target_version = 1
        def _transform(self, e): return e
    reg.register(Down)
    print("ES-14 downgrade 3->1 accepted, chain:", [c.__name__ for c in reg.resolve("Y", 3)])


def es15():
    class Evt(DomainEvent):
        name: str
        email: str = "missing"
        event_version: int = 3
    ureg = UpcasterRegistry()
    class V1V2(EventUpcaster):   # forgets to bump event_version in payload
        source_type = "Evt"; source_version = 1; target_version = 2
        def _transform(self, e): return {**e, "name": e["name"] + "+v2"}
    ureg.register(V1V2)   # no 2->3 registered: gap
    reg = EventRegistry(upcaster_registry=ureg); reg.register(Evt)
    raw = {"type": "Evt", "data": {"name": "n", "event_version": 1}, "version": 1}
    e1 = reg.deserialize(raw)
    print("ES-15 gap: chain stopped at v2, model_validate ran; result event_version", e1.event_version, "name", e1.name)
    e2 = reg.deserialize(reg.serialize(e1))
    print("ES-15 re-serialize/deserialize: name", e2.name, "(double upcast)")
    # payload without event_version key -> default 3 used, claims to be v3
    e3 = reg.deserialize({"type": "Evt", "data": {"name": "n"}, "version": 1})
    print("ES-15 payload w/o event_version -> event_version", e3.event_version, "email", e3.email)


async def main():
    await es07(); await es08b(); es14(); es15()

asyncio.run(main())
