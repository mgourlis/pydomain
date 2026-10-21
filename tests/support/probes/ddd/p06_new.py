"""New-finding probes: FakeRepository aliasing + DDD-20, FakeSagaRepository re-emission, UoW None stamps."""
import asyncio
from uuid import UUID

from pydomain.cqrs.saga.state import SagaState
from pydomain.ddd import AggregateRoot, DomainError, DomainEvent
from pydomain.testing.fake_repository import FakeRepository
from pydomain.testing.fake_saga_repository import FakeSagaRepository
from pydomain.testing.fake_unit_of_work import FakeUnitOfWork


class OrderPlaced(DomainEvent):
    order_id: UUID


class OrderRenamed(DomainEvent):
    order_id: UUID


class Order(AggregateRoot[UUID]):
    status: str = "draft"
    name: str = ""

    def place_then_fail(self) -> None:
        self.status = "placed"
        self._add_event(OrderPlaced(order_id=self.id))
        raise DomainError("rejected after mutation")

    def rename(self, n: str) -> None:
        self.name = n
        self._add_event(OrderRenamed(order_id=self.id))


class InvoiceSagaState(SagaState):
    def note(self) -> None:
        self._add_event(OrderPlaced(order_id=self.id))


async def main() -> None:
    print("--- failed command leaks state + event through FakeRepository aliasing ---")
    repo: FakeRepository[Order, UUID] = FakeRepository()
    o = Order()
    await repo.save(o)
    repo.pull_events()
    loaded = await repo.get_by_id(o.id)
    try:
        loaded.place_then_fail()  # command 1 fails, never saves
    except DomainError:
        pass
    fresh = await repo.get_by_id(o.id)  # command 2
    print("command 2 sees command 1's status without any save:", fresh.status)
    fresh.rename("n")
    await repo.save(fresh)
    print("events published by command 2:", [type(e).__name__ for e in repo.pull_events()])

    print("--- FakeSagaRepository re-emits pending events ---")
    srepo = FakeSagaRepository()
    st = InvoiceSagaState(saga_type="invoice")
    st.note()
    await srepo.save(st)
    first = srepo.pull_events()
    again = await srepo.get_by_id(st.id)
    await srepo.save(again)
    second = srepo.pull_events()
    print("first save events:", len(first), "| second save (no new events) re-emits:", len(second),
          "| same event_id:", bool(second) and second[0].event_id == first[0].event_id)

    print("--- UoW stamps None tracing ids when not set ---")
    r: FakeRepository[Order, UUID] = FakeRepository()
    uow = FakeUnitOfWork(repository=r)
    async with uow:
        x = Order()
        x.rename("a")
        await r.save(x)
        await uow.commit()
    evs = uow.collect_events()
    print("collected:", [(type(e).__name__, e.correlation_id, e.causation_id) for e in evs])


asyncio.run(main())
