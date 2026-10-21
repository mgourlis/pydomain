import asyncio, anyio
from pydomain.infrastructure import bootstrap, MessageBus, InboundEventGateway
from pydomain.testing import InMemoryMessageSubscriber
log=[]
class SlowStop(InMemoryMessageSubscriber):
    async def stop(self):
        log.append("stop-begin"); await asyncio.sleep(0.2); log.append("stop-end")
class Hang(InMemoryMessageSubscriber):
    async def start(self):
        log.append("hang-start"); await asyncio.sleep(10)
async def main():
    bus=MessageBus()
    g1=InboundEventGateway(SlowStop(), bus); g2=InboundEventGateway(Hang(), bus)
    t0=anyio.current_time()
    with anyio.move_on_after(0.05):
        await bootstrap(message_bus=bus, inbound_gateways=[g1,g2])
    log.append(f"bootstrap-returned after {anyio.current_time()-t0:.2f}s")
    await asyncio.sleep(0.4)
    print(log)
async def plain():
    log.clear()
    bus=MessageBus()
    g1=InboundEventGateway(SlowStop(), bus); g2=InboundEventGateway(Hang(), bus)
    t=asyncio.create_task(bootstrap(message_bus=bus, inbound_gateways=[g1,g2]))
    await asyncio.sleep(0.05); t.cancel()
    try: await t
    except asyncio.CancelledError: log.append("cancelled")
    print(log)
anyio.run(main, backend="asyncio")
asyncio.run(plain())
