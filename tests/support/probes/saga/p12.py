import asyncio
from datetime import timedelta, datetime, UTC
from uuid import uuid4
from common import *

class Base(Saga[SagaState]):
    listens_to = [OrderCreated, ItemsReserved]
    def __init__(self, state):
        super().__init__(state)
        self.on(OrderCreated, send=lambda e: ReserveItems(order_id=e.order_id),
                compensate=lambda e: CancelReservation(order_id=e.order_id))
        self.on(ItemsReserved, send=lambda e: ChargePayment(order_id=e.order_id), step="awaiting",
                compensate=lambda e: RefundPayment(order_id=e.order_id),
                suspend=True, suspend_timeout=timedelta(hours=1))

class RetryOnTimeout(Base):
    listens_to = Base.listens_to
    async def on_timeout(self):
        self.resume()
        self.dispatch(ChargePayment(order_id="O"))

class CompThenRaise(Base):
    listens_to = Base.listens_to
    async def on_timeout(self):
        await self.execute_compensations()
        raise RuntimeError("timeout hook failed after queuing compensations")

async def run(cls, fail_types):
    bus = RecordingBus(fail_types=fail_types)
    mgr, bus, repo = make_manager(cls, bus=bus)
    cid = uuid4()
    bus.fail_types = set()
    await mgr.handle(OrderCreated(order_id="O", correlation_id=cid))
    await mgr.handle(ItemsReserved(order_id="O", correlation_id=cid))
    st = await only_state(repo)
    repo._store[st.id].timeout_at = datetime.now(UTC) - timedelta(seconds=1)
    bus.sent.clear(); bus.fail_types = fail_types
    await mgr.process_timeouts()
    st = await only_state(repo)
    print(f"12 {cls.__name__}: bus={bus.sent} status={st.status} pending={st.pending_commands} failed_comp={[f['command_type'] for f in st.failed_compensations]} stack={len(st.compensation_stack)}")

async def main():
    await run(RetryOnTimeout, {"ChargePayment"})
    await run(CompThenRaise, {"RefundPayment"})
asyncio.run(main())
