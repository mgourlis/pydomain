import asyncio
from uuid import uuid4, UUID
from common import *
from pydomain.cqrs.command_bus import CommandBus
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.testing import FakeUnitOfWork
from pydomain.testing.fake_repository import FakeRepository

class Reservation(AggregateRoot[UUID]):
    order_id: str = ""

class TwoStep(Saga[SagaState]):
    listens_to = [OrderCreated, ItemsReserved]
    def __init__(self, state):
        super().__init__(state)
        self.on(OrderCreated, send=lambda e: ReserveItems(order_id=e.order_id), step="reserving")
        self.on(ItemsReserved, send=lambda e: ConfirmOrder(order_id=e.order_id), complete=True)

def wire(register):
    repo = FakeRepository[Reservation, UUID]()
    confirmed = []
    async def reserve(cmd, uow):
        r = Reservation(id=uuid4(), order_id=cmd.order_id)
        r._add_event(ItemsReserved(order_id=cmd.order_id))
        await repo.save(r)
        return EmptyCommandResult()
    async def confirm(cmd, uow):
        confirmed.append(cmd.order_id)
        return EmptyCommandResult()
    register(ReserveItems, reserve, lambda: FakeUnitOfWork(repo))
    register(ConfirmOrder, confirm, lambda: FakeUnitOfWork(repo))
    return confirmed

async def main():
    # Wiring A: CommandBus (the annotated type)
    cb = CommandBus(); mb_events = MessageBus()
    confirmed = wire(cb.register)
    reg = SagaRegistry(); reg.register_saga(TwoStep)
    srepo = FakeSagaRepository()
    mgr = SagaManager(srepo, reg, cb)
    mgr.bind_to(mb_events)
    await mb_events.dispatch(OrderCreated(order_id="O-1", correlation_id=uuid4()))
    st = await only_state(srepo)
    print(f"CommandBus wiring: status={st.status} confirmed={confirmed} steps={[s.event_type for s in st.step_history]}")
    # Wiring B: MessageBus
    mb = MessageBus()
    confirmed = wire(mb.register_command)
    srepo = FakeSagaRepository()
    mgr = SagaManager(srepo, reg, mb)  # type: ignore[arg-type]
    mgr.bind_to(mb)
    await mb.dispatch(OrderCreated(order_id="O-2", correlation_id=uuid4()))
    st = await only_state(srepo)
    print(f"MessageBus wiring: status={st.status} confirmed={confirmed} steps={[s.event_type for s in st.step_history]}")
asyncio.run(main())
