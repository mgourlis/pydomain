import asyncio
from uuid import uuid4
from common import *

class Three(Saga[SagaState]):
    listens_to = [OrderCreated, ItemsReserved, ApprovalGranted, PaymentDeclined]
    def __init__(self, state):
        super().__init__(state)
        self.on(OrderCreated, send=lambda e: ReserveItems(order_id=e.order_id), compensate=lambda e: CancelReservation(order_id=e.order_id))
        self.on(ItemsReserved, send=lambda e: ChargePayment(order_id=e.order_id), compensate=lambda e: RefundPayment(order_id=e.order_id))
        self.on(ApprovalGranted, send=lambda e: ConfirmOrder(order_id=e.order_id), compensate=lambda e: NotifyCustomer(order_id=e.order_id))
        self.on(PaymentDeclined, handler=self.declined)
    async def declined(self, e):
        await self.fail("declined")

class CrashBus(RecordingBus):
    def __init__(self, crash_on): super().__init__(); self.crash_on = crash_on; self.crashed=False
    async def dispatch(self, cmd):
        if type(cmd).__name__ == self.crash_on and not self.crashed:
            self.crashed = True
            self.sent.append(type(cmd).__name__ + "!CRASH")
            raise KeyboardInterrupt("process died")
        return await super().dispatch(cmd)

async def setup(bus):
    mgr, bus, repo = make_manager(Three, bus=bus)
    cid = uuid4()
    for ev in (OrderCreated(order_id="O", correlation_id=cid), ItemsReserved(order_id="O", correlation_id=cid), ApprovalGranted(order_id="O", correlation_id=cid)):
        await mgr.handle(ev)
    bus.sent.clear()
    return mgr, bus, repo, cid

async def main():
    # continue-after-failure
    bus = RecordingBus(fail_types={"RefundPayment"})
    mgr, bus, repo, cid = await setup(bus)
    await mgr.handle(PaymentDeclined(order_id="O", correlation_id=cid))
    st = await only_state(repo)
    print(f"09 middle compensation fails: bus={bus.sent} status={st.status} failed={[f['command_type'] for f in st.failed_compensations]}")
    # crash mid-compensation
    bus = CrashBus("RefundPayment")
    mgr, bus, repo, cid = await setup(bus)
    ev = PaymentDeclined(order_id="O", correlation_id=cid)
    try:
        await mgr.handle(ev)
    except KeyboardInterrupt:
        pass
    st = await only_state(repo)
    print(f"09 after crash: persisted status={st.status} stack={len(st.compensation_stack)} event processed={st.is_event_processed(ev.event_id)} bus={bus.sent}")
    mgr2, _, _ = make_manager(Three, bus=bus, repo=repo)
    bus.sent.clear()
    await mgr2.recover_pending_sagas()
    print(f"09 recover_pending_sagas after crash dispatches: {bus.sent}")
    await mgr2.handle(ev)  # broker redelivers
    print(f"09 redelivery after crash dispatches: {bus.sent}")
    # SAGA-10 hand-built
    reg = SagaRegistry(); reg.register_saga(Three)
    r = FakeSagaRepository(); b = RecordingBus()
    m = SagaManager(r, reg, b)
    s = SagaState(id=uuid4(), saga_type="Three", correlation_id=uuid4(), status=SagaStatus.COMPENSATING,
                  failed_compensations=[{"command_type": "RefundPayment", "data": {}, "module_name": "x", "error": "boom"}])
    await r.save(s)
    await m.recover_pending_sagas()
    s2 = await r.get_by_id(s.id)
    print(f"10 COMPENSATING+empty stack+failed_compensations -> {s2.status}; bus={b.sent}")
asyncio.run(main())
