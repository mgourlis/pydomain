import asyncio, logging, gc
from pydomain.infrastructure import bootstrap, MessageBus
from pydomain.infrastructure import bootstrap as bmod
import sys; B = sys.modules["pydomain.infrastructure.bootstrap"]
from pydomain.infrastructure.message_subscriber import InboundEventGateway
from pydomain.testing import InMemoryMessageSubscriber
logging.basicConfig(level=logging.WARNING, format="LOG %(name)s %(levelname)s %(message)s")
class P(InMemoryMessageSubscriber):
    def __init__(s, n, log, gate=None, sraise=None, stopraise=None, block=False, on_start=None):
        super().__init__(); s.n=n; s.log=log; s.gate=gate; s.sraise=sraise; s.stopraise=stopraise; s.block=block; s.se=asyncio.Event(); s.te=asyncio.Event(); s.on_start=on_start
    async def start(s):
        s.log.append(s.n+".start"); s.se.set()
        if s.on_start: s.on_start()
        if s.sraise: raise s.sraise
        if s.block: await asyncio.Event().wait()
    async def stop(s):
        s.log.append(s.n+".stop"); s.te.set()
        if s.gate: await s.gate.wait()
        s.log.append(s.n+".stopped")
        if s.stopraise: raise s.stopraise
        await super().stop()
async def settle():
    for _ in range(20): await asyncio.sleep(0)

async def b45_double_cancel():
    print("--- B-45 double cancel during partial-start clean-up")
    log=[]; bus=MessageBus(); g=asyncio.Event()
    a=P("a",log,gate=g,stopraise=OSError("secret-a")); b=P("b",log,stopraise=ValueError("secret-b")); c=P("c",log,sraise=ConnectionError("start"))
    t=asyncio.ensure_future(bootstrap(message_bus=bus,inbound_gateways=[InboundEventGateway(x,bus) for x in (a,b,c)]))
    await a.te.wait(); t.cancel(); await settle(); print("  done after 1st cancel?", t.done()); t.cancel(); await settle()
    print("  done after 2nd cancel?", t.done())
    try: await t
    except BaseException as e:
        ctx=e.__context__; print("  raised", type(e).__name__, "ctx", type(ctx).__name__, "ctx.ctx", type(getattr(ctx,'__context__',None)).__name__)
    g.set(); await settle(); print("  log", log, "sets", len(B._CLEAN_UPS), len(B._ABANDONED))

async def mutate_list():
    print("--- caller list mutated during start")
    log=[]; bus=MessageBus(); other=MessageBus(); lst=[]
    rogue=InboundEventGateway(P("rogue",log), other)
    a=P("a",log,on_start=lambda: lst.append(rogue))
    lst.append(InboundEventGateway(a,bus))
    try:
        app=await bootstrap(message_bus=bus, inbound_gateways=lst); print("  started:", log, "app gateways", len(app.inbound_gateways))
    except Exception as e: print("  ", type(e).__name__, e)

async def dup_order():
    print("--- duplicate + mismatch ordering")
    bus=MessageBus(); g=InboundEventGateway(P("a",[]),bus)
    for lst in ([g,g], [g, InboundEventGateway(P("x",[]),MessageBus()), g]):
        try: await bootstrap(message_bus=bus, inbound_gateways=lst)
        except ValueError as e: print("  ", e)

async def race_done_then_cancel():
    print("--- cleanup finishes, then cancel before bootstrap resumes (B-40 path)")
    log=[]; bus=MessageBus(); g=asyncio.Event()
    a=P("a",log,gate=g,stopraise=RuntimeError("secret")); b=P("b",log,block=True)
    t=asyncio.ensure_future(bootstrap(message_bus=bus,inbound_gateways=[InboundEventGateway(a,bus),InboundEventGateway(b,bus)]))
    await b.se.wait(); t.cancel(); await a.te.wait()
    g.set()
    # let cleanup task finish but cancel bootstrap in the same iteration
    for i in range(6):
        await asyncio.sleep(0)
        ct=[x for x in B._CLEAN_UPS]
        if not ct: t.cancel(); break
    try: await t
    except BaseException as e: print("  raised", type(e).__name__, "notes", getattr(e,'__notes__',None))
    await settle()

async def main():
    await b45_double_cancel(); await mutate_list(); await dup_order(); await race_done_then_cancel()
asyncio.run(main())
