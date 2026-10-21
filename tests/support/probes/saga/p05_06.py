import asyncio
from datetime import timedelta, datetime, UTC
from uuid import uuid4
from common import *

class Susp(Saga[SagaState]):
    listens_to = [OrderCreated, ApprovalGranted]
    def __init__(self, state):
        super().__init__(state)
        self.on(OrderCreated, send=lambda e: ReserveItems(order_id=e.order_id), step="awaiting",
                suspend=True, suspend_timeout=timedelta(hours=1))
        self.on(ApprovalGranted, send=lambda e: ConfirmOrder(order_id=e.order_id), complete=True)

async def saga05():
    mgr, bus, repo = make_manager(Susp)
    cid = uuid4()
    e1 = OrderCreated(order_id="O-1", correlation_id=cid)
    await mgr.handle(e1)
    st = await only_state(repo)
    print(f"05 after e1: status={st.status} timeout_at set={st.timeout_at is not None} causation==e1={st.causation_id==e1.event_id}")
    await mgr.handle(e1)  # duplicate redelivery
    st = await only_state(repo)
    print(f"05 after DUPLICATE e1: status={st.status} timeout_at={st.timeout_at} suspension_reason={st.suspension_reason} bus={bus.sent}")
    # retry guard via duplicate
    bus2 = RecordingBus(fail_types={"ReserveItems"})
    mgr2, bus2, repo2 = make_manager(Susp, bus=bus2)
    cid2 = uuid4(); e2 = OrderCreated(order_id="O-2", correlation_id=cid2)
    await mgr2.handle(e2)
    try:
        await mgr2.recover_pending_sagas()
    except RuntimeError as exc:
        print("05b recover_pending_sagas RAISED:", exc)
    st = await only_state(repo2)
    print(f"05b after 1 dispatch failure + 1 recovery: status={st.status} retry={st.retry_count}")
    # put the saga back in a non-pending suspended state? just redeliver duplicate e2
    await mgr2.handle(e2)
    st = await only_state(repo2)
    print(f"05b after DUPLICATE e2: status={st.status} error={st.error!r}")

async def saga06():
    mgr, bus, repo = make_manager(Susp)
    cid = uuid4()
    await mgr.handle(OrderCreated(order_id="O-6", correlation_id=cid))
    st = await only_state(repo)
    # expire it in storage (simulate time passing)
    repo._store[st.id].timeout_at = datetime.now(UTC) - timedelta(seconds=5)
    await mgr.handle(ApprovalGranted(order_id="O-6", correlation_id=cid))
    st = await only_state(repo)
    print(f"06 late ApprovalGranted after timeout_at: status={st.status} bus={bus.sent}")

class SlowBus(RecordingBus):
    async def dispatch(self, cmd):
        await asyncio.sleep(0.01)
        return await super().dispatch(cmd)

class Susp2(Susp):
    listens_to = [OrderCreated, ApprovalGranted]
    def __init__(self, state):
        super().__init__(state)
        self.on(OrderCreated, send=lambda e: ReserveItems(order_id=e.order_id), step="awaiting",
                suspend=True, suspend_timeout=timedelta(hours=1),
                compensate=lambda e: CancelReservation(order_id=e.order_id))

async def saga06_race():
    bus = SlowBus()
    mgr, bus, repo = make_manager(Susp2, bus=bus)
    cid = uuid4()
    await mgr.handle(OrderCreated(order_id="O-7", correlation_id=cid))
    st = await only_state(repo)
    repo._store[st.id].timeout_at = datetime.now(UTC) - timedelta(seconds=5)
    bus.sent.clear()
    await asyncio.gather(mgr.process_timeouts(), mgr.handle(ApprovalGranted(order_id="O-7", correlation_id=cid)))
    st = await only_state(repo)
    print(f"06 race event||timeout: bus={bus.sent} final status={st.status}")

async def main():
    await saga05(); await saga06(); await saga06_race()
asyncio.run(main())
