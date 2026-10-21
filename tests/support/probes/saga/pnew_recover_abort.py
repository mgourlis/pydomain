import asyncio
from uuid import uuid4
from common import *

class S(Saga[SagaState]):
    listens_to = [OrderCreated]
    def __init__(self, state):
        super().__init__(state)
        self.on(OrderCreated, send=lambda e: ReserveItems(order_id=e.order_id))

class PerOrderBus(RecordingBus):
    def __init__(self): super().__init__(); self.down = {"A", "B"}
    async def dispatch(self, cmd):
        if cmd.order_id in self.down:
            self.sent.append(f"{cmd.order_id}!FAILED"); raise RuntimeError("down")
        self.sent.append(cmd.order_id)

async def main():
    bus = PerOrderBus()
    mgr, bus, repo = make_manager(S, bus=bus)
    await mgr.handle(OrderCreated(order_id="A", correlation_id=uuid4()))
    await mgr.handle(OrderCreated(order_id="B", correlation_id=uuid4()))
    bus.down = {"A"}; bus.sent.clear()       # B's downstream is healthy again, A's is not
    try:
        await mgr.recover_pending_sagas()
    except RuntimeError as e:
        print("recover_pending_sagas raised:", e)
    print("dispatched during recovery:", bus.sent, "| B still pending:", any(s.pending_commands for s in repo._store.values() if s.pending_commands and s.pending_commands[0]['data']['order_id']=='B'))
asyncio.run(main())
