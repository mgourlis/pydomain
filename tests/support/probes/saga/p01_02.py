import asyncio
from uuid import uuid4
from common import *

class FailWithCompSaga(Saga[SagaState]):
    listens_to = [OrderCreated, PaymentDeclined]
    def __init__(self, state):
        super().__init__(state)
        self.on(OrderCreated, send=lambda e: ReserveItems(order_id=e.order_id),
                step="reserving", compensate=lambda e: CancelReservation(order_id=e.order_id))
        self.on(PaymentDeclined, send=lambda e: ChargePayment(order_id=e.order_id),
                step="charging", compensate=lambda e: RefundPayment(order_id=e.order_id), fail=True)

class Adr055Saga(Saga[SagaState]):
    """ADR-055 motivating example: notify, then fail with compensation."""
    listens_to = [OrderCreated, PaymentDeclined]
    def __init__(self, state):
        super().__init__(state)
        self.on(OrderCreated, send=lambda e: ReserveItems(order_id=e.order_id),
                step="reserving", compensate=lambda e: CancelReservation(order_id=e.order_id))
        self.on(PaymentDeclined, send=lambda e: NotifyCustomer(order_id=e.order_id), fail=True,
                fail_reason="rejected")

class FailNoStackSaga(Saga[SagaState]):
    listens_to = [OrderCreated]
    def __init__(self, state):
        super().__init__(state)
        self.on(OrderCreated, send=lambda e: NotifyCustomer(order_id=e.order_id), fail=True)

class RaiseAfterDispatchSaga(Saga[SagaState]):
    listens_to = [OrderCreated]
    def __init__(self, state):
        super().__init__(state)
        self.on(OrderCreated, handler=self.h)
    async def h(self, e):
        self.dispatch(ChargePayment(order_id=e.order_id))
        raise ValueError("handler blew up after dispatch")

async def run(cls, events):
    mgr, bus, repo = make_manager(cls)
    for ev in events:
        await mgr.handle(ev)
    st = await only_state(repo)
    print(f"{cls.__name__}: bus={bus.sent} status={st.status} stack={len(st.compensation_stack)} failed_comp={st.failed_compensations}")

async def main():
    cid = uuid4()
    await run(FailWithCompSaga, [OrderCreated(order_id="O-1", correlation_id=cid), PaymentDeclined(order_id="O-1", correlation_id=cid)])
    cid = uuid4()
    await run(Adr055Saga, [OrderCreated(order_id="O-2", correlation_id=cid), PaymentDeclined(order_id="O-2", correlation_id=cid)])
    cid = uuid4()
    await run(FailNoStackSaga, [OrderCreated(order_id="O-3", correlation_id=cid)])
    cid = uuid4()
    await run(RaiseAfterDispatchSaga, [OrderCreated(order_id="O-4", correlation_id=cid)])
asyncio.run(main())
