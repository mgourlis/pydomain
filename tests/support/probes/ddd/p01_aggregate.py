"""DDD-01, DDD-02, DDD-20, A13 probes."""
import asyncio
import copy
import pickle
from uuid import UUID, uuid4

from pydantic import ConfigDict

from pydomain.ddd import AggregateRoot, DomainError, DomainEvent
from pydomain.testing.fake_repository import FakeRepository


class OrderPlaced(DomainEvent):
    order_id: UUID


class Order(AggregateRoot[UUID]):
    status: str = "draft"
    lines: list[int] = []

    def place(self) -> None:
        self.status = "placed"
        self._add_event(OrderPlaced(order_id=self.id))

    def place_then_fail(self) -> None:
        self.status = "placed"
        self._add_event(OrderPlaced(order_id=self.id))
        raise DomainError("invariant check after mutation")


print("--- DDD-01 model_copy ---")
o = Order()
o.place()
c = o.model_copy()
print("copy shares list object:", c._pending_events is o._pending_events)
c.place()
print("original pending after copy.place():", len(o._pending_events), "copy:", len(c._pending_events))
o2 = Order()
o2.place()
cd = o2.model_copy(deep=True)
print("deep copy shares list:", cd._pending_events is o2._pending_events, "deep copy pending:", len(cd._pending_events))
# pull on original after model_copy: does copy still hold events?
o3 = Order(); o3.place(); c3 = o3.model_copy()
ev = o3.pull_events()
print("after original pull_events: copy still pending:", len(c3._pending_events), "pulled:", len(ev))

print("--- DDD-02 deepcopy / pickle / model_validate ---")
o4 = Order(); o4.place()
print("copy.copy pending:", len(copy.copy(o4)._pending_events), "shares:", copy.copy(o4)._pending_events is o4._pending_events)
print("copy.deepcopy pending:", len(copy.deepcopy(o4)._pending_events))
print("pickle round-trip pending:", len(pickle.loads(pickle.dumps(o4))._pending_events))
print("model_validate(model_dump()) pending:", len(Order.model_validate(o4.model_dump())._pending_events))
print("model_dump contains pending?", "_pending_events" in o4.model_dump())

print("--- DDD-20 mutate then raise ---")
o5 = Order()
try:
    o5.place_then_fail()
except DomainError:
    pass
print("after raise: status =", o5.status, "pending =", len(o5._pending_events))


async def frozen_probe() -> None:
    print("--- A13 frozen aggregate ---")

    class FrozenOrder(AggregateRoot[UUID]):
        model_config = ConfigDict(frozen=True)
        status: str = "draft"

    try:
        f = FrozenOrder()
    except Exception as e:  # noqa: BLE001
        print("construct failed:", type(e).__name__, e)
        return
    print("construct ok; hashable:", hash(f) is not None)
    f._add_event(OrderPlaced(order_id=f.id))
    print("_add_event ok on frozen; pending", len(f._pending_events))
    repo: FakeRepository[FrozenOrder, UUID] = FakeRepository()
    await repo.save(f)
    print("insert ok")
    try:
        await repo.save(f)
        print("update ok, version", f.version)
    except Exception as e:  # noqa: BLE001
        print("update FAILED:", type(e).__name__, str(e).splitlines()[0:3])


asyncio.run(frozen_probe())
