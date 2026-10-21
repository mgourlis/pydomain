import asyncio
from uuid import uuid4
from common import *

class Fwd(Saga[SagaState]):
    listens_to = [OrderCreated, ItemsReserved]
    def __init__(self, state):
        super().__init__(state)
        self.on(OrderCreated, handler=self.two)
        self.on(ItemsReserved, send=lambda e: ConfirmOrder(order_id=e.order_id))
    async def two(self, e):
        self.dispatch(ReserveItems(order_id=e.order_id))
        self.dispatch(ChargePayment(order_id=e.order_id))
        self.add_compensation(CancelReservation(order_id=e.order_id))

class ZeroState(SagaState):
    max_retries: int = 0

class FwdZero(Fwd):
    state_class = ZeroState
    listens_to = [OrderCreated, ItemsReserved]

async def a():
    # real path: dispatch of ReserveItems fails -> both commands pending; then "rename deploy" breaks ReserveItems hydration
    bus = RecordingBus(fail_types={"ReserveItems"}, fail_times=1)
    mgr, bus, repo = make_manager(Fwd, bus=bus)
    await mgr.handle(OrderCreated(order_id="O", correlation_id=uuid4()))
    st = await only_state(repo)
    repo._store[st.id].pending_commands[0]["module_name"] = "renamed.module"
    print("11a pending before recovery:", [(p["command_type"], p["dispatched"]) for p in repo._store[st.id].pending_commands])
    await mgr.recover_pending_sagas()
    st = await only_state(repo)
    print(f"11a after recovery: bus={bus.sent} pending={st.pending_commands} retry={st.retry_count} failed_comp={st.failed_compensations} status={st.status} error={st.error}")

async def b():
    bus = RecordingBus(fail_types={"ReserveItems"})
    mgr, bus, repo = make_manager(Fwd, bus=bus)
    await mgr.handle(OrderCreated(order_id="O", correlation_id=uuid4()))
    st = await only_state(repo); print(f"11b after 1 forward dispatch failure: retry={st.retry_count}")
    for i in range(3):
        try: await mgr.recover_pending_sagas()
        except RuntimeError: pass
        st = await only_state(repo)
        print(f"11b after recovery attempt {i+1}: retry={st.retry_count} status={st.status} bus_attempts={bus.sent.count('ReserveItems!FAILED')}")

async def c():
    bus = RecordingBus(fail_types={"ReserveItems"})
    mgr, bus, repo = make_manager(FwdZero, bus=bus)
    cid = uuid4()
    for i in range(4):
        await mgr.handle(OrderCreated(order_id="O", correlation_id=cid))  # new event ids each time
    st = await only_state(repo)
    print(f"11c max_retries=0, handle path after 4 failing events: status={st.status} retry={st.retry_count}")
    await mgr.recover_pending_sagas()
    st = await only_state(repo)
    print(f"11c max_retries=0, recovery path: status={st.status} error={st.error!r}")

async def d():
    for path in ("handle", "recover"):
        bus = RecordingBus(fail_types={"ReserveItems"})
        mgr, bus, repo = make_manager(Fwd, bus=bus)
        cid = uuid4()
        await mgr.handle(OrderCreated(order_id="O", correlation_id=cid))
        st = await only_state(repo)
        repo._store[st.id].retry_count = 3
        bus.sent.clear()
        if path == "handle":
            await mgr.handle(ItemsReserved(order_id="O", correlation_id=cid))
        else:
            await mgr.recover_pending_sagas()
        st = await only_state(repo)
        print(f"11d exhausted via {path}: status={st.status} bus={bus.sent} stack_left={len(st.compensation_stack)}")

async def e():
    bus = RecordingBus(fail_types={"ReserveItems"}, fail_times=1)
    mgr, bus, repo = make_manager(Fwd, bus=bus)
    cid = uuid4()
    ev1 = OrderCreated(order_id="O", correlation_id=cid)
    await mgr.handle(ev1)          # ReserveItems fails; both pending, saga SUSPENDED
    ev2 = ItemsReserved(order_id="O", correlation_id=cid)
    await mgr.handle(ev2)          # resumes, dispatches ConfirmOrder (newer batch)
    await mgr.recover_pending_sagas()  # dispatches older batch
    st = await only_state(repo)
    order = [type(c).__name__ for c in bus.sent_cmds]
    caus = {type(c).__name__: ("ev1" if c.causation_id == ev1.event_id else "ev2" if c.causation_id == ev2.event_id else "state.id" if c.causation_id == st.id else str(c.causation_id)) for c in bus.sent_cmds}
    print(f"11e dispatch order={order} causation={caus}")

async def main():
    await a(); await b(); await c(); await d(); await e()
asyncio.run(main())
