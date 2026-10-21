import asyncio
from uuid import uuid4
from common import *
from pydomain.ddd.exceptions import ConcurrencyError

class TwoStep(Saga[SagaState]):
    listens_to = [OrderCreated, ItemsReserved]
    def __init__(self, state):
        super().__init__(state)
        self.on(OrderCreated, send=lambda e: ReserveItems(order_id=e.order_id), step="reserving",
                compensate=lambda e: CancelReservation(order_id=e.order_id))
        self.on(ItemsReserved, send=lambda e: ConfirmOrder(order_id=e.order_id), complete=True)

class VersionLog(FakeSagaRepository):
    def __init__(self): super().__init__(); self.log=[]
    async def save(self, st):
        prev = self._store.get(st.id)
        self.log.append((prev.version if prev else None, st.version))
        await super().save(st)

class Boom(FakeSagaRepository):
    def __init__(self): super().__init__(); self.n=0
    async def save(self, st):
        self.n += 1
        if self.n == 3: raise ConcurrencyError("simulated conflict on save #3")
        await super().save(st)

async def main():
    cid = uuid4()
    mgr, bus, repo = make_manager(TwoStep, repo=VersionLog())
    await mgr.handle(OrderCreated(order_id="O-1", correlation_id=cid))
    await mgr.handle(ItemsReserved(order_id="O-1", correlation_id=cid))
    print("07 (stored_version, given_version) per save:", repo.log)
    vrepo = VersionCheckingSagaRepo()
    mgr, bus, _ = make_manager(TwoStep, repo=vrepo)
    cid = uuid4()
    await mgr.handle(OrderCreated(order_id="O-2", correlation_id=cid))
    await mgr.handle(ItemsReserved(order_id="O-2", correlation_id=cid))
    st = await only_state(vrepo)
    print(f"07 with OCC repo: conflicts={vrepo.conflicts} bus={bus.sent} final status={st.status}")
    # SAGA-08
    mgr, bus, brepo = make_manager(TwoStep, repo=Boom())
    cid = uuid4()
    r = await mgr.handle(OrderCreated(order_id="O-3", correlation_id=cid))
    st = await only_state(brepo)
    print(f"08 handle() returned {r!r} (no raise) after ConcurrencyError; bus={bus.sent} stored status={st.status} pending={st.pending_commands and [p['dispatched'] for p in st.pending_commands]}")
    bus2 = RecordingBus(fail_types={"ReserveItems"})
    mgr, bus2, repo2 = make_manager(TwoStep, bus=bus2)
    r = await mgr.handle(OrderCreated(order_id="O-4", correlation_id=uuid4()))
    print(f"08 bus failure: handle() returned {r!r} (no raise)")
asyncio.run(main())
