import asyncio
from uuid import uuid4
from common import *

class S(Saga[SagaState]):
    listens_to = [OrderCreated, ItemsReserved]
    def __init__(self, state):
        super().__init__(state)
        self.on(OrderCreated, send=lambda e: ReserveItems(order_id=e.order_id))
        self.on(ItemsReserved, send=lambda e: ConfirmOrder(order_id=e.order_id), complete=True)

class YieldingRepo(FakeSagaRepository):
    """Same as the fake, but find yields to the loop like real I/O does."""
    async def find_by_correlation_id(self, cid, saga_type):
        r = await super().find_by_correlation_id(cid, saga_type)
        await asyncio.sleep(0)
        return r

async def main():
    # (a) concurrent first events
    for repo_cls in (FakeSagaRepository, YieldingRepo):
        mgr, bus, repo = make_manager(S, repo=repo_cls())
        cid = uuid4()
        await asyncio.gather(mgr.handle(OrderCreated(order_id="A", correlation_id=cid)),
                             mgr.handle(OrderCreated(order_id="B", correlation_id=cid)))
        print(f"13a {repo_cls.__name__}: saga rows for one correlation={len(repo._store)} bus={bus.sent}")
    # (b) terminal blocks new
    mgr, bus, repo = make_manager(S)
    cid = uuid4()
    await mgr.handle(OrderCreated(order_id="A", correlation_id=cid))
    await mgr.handle(ItemsReserved(order_id="A", correlation_id=cid))
    bus.sent.clear()
    await mgr.handle(OrderCreated(order_id="A2", correlation_id=cid))
    print(f"13b new OrderCreated on completed correlation: bus={bus.sent} rows={len(repo._store)}")
    # (c) no correlation
    mgr, bus, repo = make_manager(S)
    ev = OrderCreated(order_id="C")
    await mgr.handle(ev)
    print(f"13c event without correlation_id: rows={len(repo._store)} bus={bus.sent}")
    # (d) start_saga twice with same uncorrelated event
    r1 = await mgr.start_saga(S, ev); r2 = await mgr.start_saga(S, ev)
    corr = [s.correlation_id for s in repo._store.values()]
    print(f"13d start_saga x2 same event: ids differ={r1!=r2} rows={len(repo._store)} bus={bus.sent} correlation==event_id={[c==ev.event_id for c in corr]}")
asyncio.run(main())
