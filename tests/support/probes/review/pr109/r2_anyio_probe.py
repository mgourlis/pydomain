import asyncio, logging, time, anyio
from pydomain.infrastructure import bootstrap, MessageBus, InboundEventGateway
from pydomain.testing import InMemoryMessageSubscriber, InMemoryMessageBroker

records=[]
class H(logging.Handler):
    def emit(self, r): records.append((r.name, r.levelname, r.getMessage()))
logging.getLogger().addHandler(H()); logging.getLogger().setLevel(logging.INFO)

class SlowStop(InMemoryMessageSubscriber):
    def __init__(self, d=0.3, fail=False): super().__init__(); self.d=d; self.fail=fail; self.stopped_at=None
    async def stop(self):
        await asyncio.sleep(self.d); self.stopped_at=time.monotonic()
        if self.fail: raise RuntimeError("secret-payload")
class Hang(InMemoryMessageSubscriber):
    async def start(self): await asyncio.sleep(10)
class Boom(InMemoryMessageSubscriber):
    async def start(self): raise OSError("start-failed")

def gws(*subs):
    bus=MessageBus(); return bus,[InboundEventGateway(s,bus) for s in subs]

async def p1_timeout():
    s=SlowStop(0.3); bus,g=gws(s,Hang())
    t0=time.monotonic()
    try:
        async with asyncio.timeout(0.05):
            await bootstrap(message_bus=bus, inbound_gateways=g)
    except BaseException as e:
        print("P1 asyncio.timeout:", type(e).__name__, f"after {time.monotonic()-t0:.2f}s; stop done:", s.stopped_at is not None)

async def p2_anyio():
    records.clear()
    s=SlowStop(0.2, fail=True); bus,g=gws(s,Hang())
    t0=anyio.current_time()
    with anyio.move_on_after(0.05):
        await bootstrap(message_bus=bus, inbound_gateways=g)
    el=anyio.current_time()-t0
    await asyncio.sleep(0.4)
    print("P2 anyio: returned after %.2fs; stop done later: %s; logs: %s" % (el, s.stopped_at is not None, [r for r in records if r[0]=='pydomain.bootstrap' and r[1]=='WARNING']))
    print("P2 secret in any log:", any("secret-payload" in r[2] for r in records))

async def p2b_anyio_b45():
    records.clear()
    s=SlowStop(0.2, fail=True); bus,g=gws(s,Boom())
    try:
        with anyio.fail_after(0.0001):
            await bootstrap(message_bus=bus, inbound_gateways=g)
    except BaseException as e:
        print("P2b anyio fail_after on failing start:", type(e).__name__, repr(e)[:80])

async def p3_b45():
    s=SlowStop(0.2, fail=True); bus,g=gws(s,Boom())
    task=asyncio.create_task(bootstrap(message_bus=bus, inbound_gateways=g))
    await asyncio.sleep(0.05); task.cancel()
    try: await task
    except BaseException as e:
        ctx=e.__context__
        print("P3 B-45:", type(e).__name__, "ctx:", type(ctx).__name__, getattr(ctx,'__notes__',None), "stop done:", s.stopped_at is not None)

async def p4_second_cancel():
    records.clear()
    s=SlowStop(0.3, fail=True); bus,g=gws(s,Hang())
    task=asyncio.create_task(bootstrap(message_bus=bus, inbound_gateways=g))
    await asyncio.sleep(0.05); task.cancel(); await asyncio.sleep(0.05); task.cancel()
    t0=time.monotonic()
    try: await task
    except BaseException as e: print("P4 second cancel:", type(e).__name__, f"waited {time.monotonic()-t0:.2f}s", "stop done yet:", s.stopped_at is not None)
    await asyncio.sleep(0.5)
    print("P4 logs:", [r for r in records if r[1]=='WARNING'], "stop done:", s.stopped_at is not None)

async def p5_dup_copy():
    bus,(a,)=gws(InMemoryMessageSubscriber())
    try: await bootstrap(message_bus=bus, inbound_gateways=[a,a])
    except ValueError as e: print("P5 dup:", e)
    lst=[a]; app=await bootstrap(message_bus=bus, inbound_gateways=lst); lst.clear(); r=app.inbound_gateways; r.clear()
    print("P5 copy:", len(app.inbound_gateways), app.inbound_gateways is app.inbound_gateways)
    await app.shutdown()

async def p6_kbint():
    class KI(InMemoryMessageSubscriber):
        async def start(self): raise KeyboardInterrupt
    s=SlowStop(0.01); bus,g=gws(s,KI())
    try: await bootstrap(message_bus=bus, inbound_gateways=g)
    except KeyboardInterrupt: print("P6 KeyboardInterrupt: stop ran:", s.stopped_at is not None)

async def p7_shutdown_cancel():
    s1=SlowStop(0.3); s2=SlowStop(0.01); bus,g=gws(s1,s2)
    app=await bootstrap(message_bus=bus, inbound_gateways=g)
    t=asyncio.create_task(app.shutdown()); await asyncio.sleep(0.05); t.cancel()
    try: await t
    except asyncio.CancelledError: print("P7 shutdown cancelled: second stop ran:", s2.stopped_at is not None)

async def main():
    await p1_timeout(); await p3_b45(); await p4_second_cancel(); await p5_dup_copy(); await p7_shutdown_cancel()
asyncio.run(main())
anyio.run(p2_anyio, backend="asyncio")
anyio.run(p2b_anyio_b45, backend="asyncio")
try:
    asyncio.run(p6_kbint())
except KeyboardInterrupt: print("P6 KI escaped asyncio.run")
