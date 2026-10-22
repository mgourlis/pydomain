import asyncio
from uuid import uuid4, UUID
from common import *
from p19 import TwoStep, wire  # reuses wiring (p19 main runs on import; harmless)
from pydomain.infrastructure.message_bus import MessageBus

class TracingRepo(FakeSagaRepository):
    def __init__(self):
        super().__init__(); self.log = []
    async def save(self, state):
        self.log.append((state.status.value, [s.event_type for s in state.step_history]))
        await super().save(state)

async def main():
    mb = MessageBus()
    confirmed = wire(mb.register_command)
    reg = SagaRegistry(); reg.register_saga(TwoStep)
    srepo = TracingRepo()
    mgr = SagaManager(srepo, reg, mb)
    mgr.bind_to(mb)
    await mb.dispatch(OrderCreated(order_id="O-3", correlation_id=uuid4()))
    for i, row in enumerate(srepo.log): print("save", i, row)
    st = await only_state(srepo)
    print("FINAL:", st.status, "confirmed:", confirmed)
asyncio.run(main())
