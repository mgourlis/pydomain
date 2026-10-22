import asyncio
from datetime import timedelta
from uuid import uuid4
from common import *

class CompleteSaga(Saga[SagaState]):
    listens_to = [OrderCreated]
    def __init__(self, state):
        super().__init__(state)
        self.on(OrderCreated, send=lambda e: ConfirmOrder(order_id=e.order_id), complete=True)

class FailSaga(Saga[SagaState]):
    listens_to = [OrderCreated]
    def __init__(self, state):
        super().__init__(state)
        self.on(OrderCreated, send=lambda e: NotifyCustomer(order_id=e.order_id), fail=True)

class SuspendTimeoutSaga(Saga[SagaState]):
    listens_to = [OrderCreated, ApprovalGranted]
    def __init__(self, state):
        super().__init__(state)
        self.on(OrderCreated, send=lambda e: ReserveItems(order_id=e.order_id), step="awaiting",
                suspend=True, suspend_timeout=timedelta(hours=1))
        self.on(ApprovalGranted, send=lambda e: ConfirmOrder(order_id=e.order_id), complete=True)

class GatedSaga(Saga[SagaState]):
    """Step 1 dispatches ReserveItems; ItemsReserved continues; ApprovalGranted only resumes 'awaiting'."""
    listens_to = [OrderCreated, ItemsReserved, ApprovalGranted]
    def __init__(self, state):
        super().__init__(state)
        self.on(OrderCreated, send=lambda e: ReserveItems(order_id=e.order_id), step="reserving")
        self.on(ItemsReserved, send=lambda e: ChargePayment(order_id=e.order_id), step="charging")
        self.on(ApprovalGranted, send=lambda e: ConfirmOrder(order_id=e.order_id),
                resumes_from="awaiting", complete=True)

async def one(cls, failtype, extra=None):
    bus = RecordingBus(fail_types={failtype}, fail_times=1)
    mgr, bus, repo = make_manager(cls, bus=bus)
    cid = uuid4()
    await mgr.handle(OrderCreated(order_id="O-1", correlation_id=cid))
    st = await only_state(repo)
    print(f"{cls.__name__} after bus failure: status={st.status} timeout_at={st.timeout_at} completed_at={st.completed_at is not None} retry={st.retry_count} reason={st.suspension_reason!r}")
    await mgr.recover_pending_sagas()
    st = await only_state(repo)
    print(f"   after recover_pending_sagas: bus={bus.sent} status={st.status} pending={len(st.pending_commands)} retry={st.retry_count} timeout_at={st.timeout_at}")
    await mgr.process_timeouts()
    st = await only_state(repo)
    print(f"   after process_timeouts: status={st.status}")
    if extra:
        for ev in extra(cid):
            await mgr.handle(ev)
        st = await only_state(repo)
        print(f"   after next events {[type(e).__name__ for e in extra(cid)]}: bus={bus.sent} status={st.status}")

async def main():
    await one(CompleteSaga, "ConfirmOrder", lambda cid: [OrderCreated(order_id="O-1", correlation_id=cid)])
    await one(FailSaga, "NotifyCustomer")
    await one(SuspendTimeoutSaga, "ReserveItems")
    await one(GatedSaga, "ReserveItems", lambda cid: [ItemsReserved(order_id="O-1", correlation_id=cid)])
asyncio.run(main())
