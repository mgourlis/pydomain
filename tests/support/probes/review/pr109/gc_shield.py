import asyncio, gc
from pydomain.infrastructure import bootstrap, MessageBus
from pydomain.infrastructure.message_subscriber import InboundEventGateway
from pydomain.testing import InMemoryMessageSubscriber
class S1(InMemoryMessageSubscriber):
    async def stop(self):
        print("stop entered"); await asyncio.get_running_loop().create_future(); print("never")
class S2(InMemoryMessageSubscriber):
    async def start(self):
        await asyncio.get_running_loop().create_future()
async def main():
    bus=MessageBus()
    t=asyncio.ensure_future(bootstrap(message_bus=bus, inbound_gateways=[InboundEventGateway(S1(),bus),InboundEventGateway(S2(),bus)]))
    for _ in range(3): await asyncio.sleep(0)
    t.cancel()
    for _ in range(3): await asyncio.sleep(0)
    t.cancel()
    try: await t
    except asyncio.CancelledError: print("outer cancelled")
    gc.collect(); await asyncio.sleep(0); print("pending tasks:", len(asyncio.all_tasks())-1)
asyncio.run(main())
