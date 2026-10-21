import asyncio, gc, sys, warnings
from pydomain.infrastructure import bootstrap, MessageBus
from pydomain.infrastructure.message_subscriber import InboundEventGateway
from pydomain.testing import InMemoryMessageSubscriber, InMemoryMessageBroker

log = []

class Sub(InMemoryMessageSubscriber):
    def __init__(self, name, start_block=False, stop_block=None, stop_raise=None, start_raise=None):
        super().__init__(); self.name=name; self.start_block=start_block; self.stop_block=stop_block
        self.stop_raise=stop_raise; self.start_raise=start_raise; self.entered=asyncio.Event(); self.stop_entered=asyncio.Event()
        self.starts=0; self.stops=0
    async def start(self):
        self.starts+=1; log.append(f"{self.name}.start")
        if self.start_raise: raise self.start_raise
        if self.start_block:
            self.entered.set(); await asyncio.Event().wait()
        await super().start()
    async def stop(self):
        self.stops+=1; log.append(f"{self.name}.stop")
        self.stop_entered.set()
        if self.stop_block is not None: await self.stop_block.wait()
        log.append(f"{self.name}.stop-done")
        if self.stop_raise: raise self.stop_raise
        await super().stop()

async def case_second_cancel():
    print("--- A: second cancellation during shielded cleanup")
    log.clear(); bus=MessageBus(); release=asyncio.Event()
    s1=Sub("s1", stop_block=release, stop_raise=RuntimeError("secret payload")); s2=Sub("s2", start_block=True)
    t=asyncio.ensure_future(bootstrap(message_bus=bus, inbound_gateways=[InboundEventGateway(s1,bus),InboundEventGateway(s2,bus)]))
    await s2.entered.wait(); t.cancel()
    await s1.stop_entered.wait(); t.cancel()
    try: await t
    except asyncio.CancelledError as e: print("  outer raised CancelledError, notes=", getattr(e,"__notes__",None))
    print("  log after 2nd cancel:", log)
    release.set()
    for _ in range(5): await asyncio.sleep(0)
    print("  log after release:", log, " s1.stopped=", s1.stopped)

async def case_timeout_hang():
    print("--- B: asyncio.timeout around bootstrap with hanging cleanup stop")
    log.clear(); bus=MessageBus(); never=asyncio.Event()
    s1=Sub("s1", stop_block=never); s2=Sub("s2", start_block=True)
    async def run():
        async with asyncio.timeout(0.05):
            await bootstrap(message_bus=bus, inbound_gateways=[InboundEventGateway(s1,bus),InboundEventGateway(s2,bus)])
    t=asyncio.ensure_future(run())
    done,_=await asyncio.wait({t}, timeout=0.5)
    print("  completed within 0.5s:", bool(done), "log:", log)
    t.cancel()
    try: await t
    except BaseException as e: print("  after extra cancel:", type(e).__name__)
    never.set(); await asyncio.sleep(0)

async def case_timeout_ok():
    print("--- B2: asyncio.timeout, finite cleanup -> TimeoutError?")
    log.clear(); bus=MessageBus()
    s1=Sub("s1"); s2=Sub("s2", start_block=True)
    try:
        async with asyncio.timeout(0.05):
            await bootstrap(message_bus=bus, inbound_gateways=[InboundEventGateway(s1,bus),InboundEventGateway(s2,bus)])
    except BaseException as e: print("  raised", type(e).__name__, "log", log, "s1.stopped", s1.stopped)

async def case_taskgroup():
    print("--- B3: TaskGroup sibling failure cancels bootstrap")
    log.clear(); bus=MessageBus()
    s1=Sub("s1"); s2=Sub("s2", start_block=True)
    async def boom():
        await s2.entered.wait(); raise KeyError("x")
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(bootstrap(message_bus=bus, inbound_gateways=[InboundEventGateway(s1,bus),InboundEventGateway(s2,bus)]))
            tg.create_task(boom())
    except BaseException as e: print("  raised", type(e).__name__, e.exceptions if hasattr(e,'exceptions') else '', "log", log)

async def case_dup():
    print("--- C: same gateway twice")
    log.clear(); bus=MessageBus(); s=Sub("s"); gw=InboundEventGateway(s,bus)
    app=await bootstrap(message_bus=bus, inbound_gateways=[gw,gw])
    print("  starts", s.starts); await app.shutdown(); print("  stops", s.stops, "log", log)

async def case_dup_fail():
    print("--- C2: same gateway twice, second start raises")
    log.clear(); bus=MessageBus()
    class Once(Sub):
        async def start(self):
            await super().start()
            if self.starts==2: raise RuntimeError("already started")
    s=Once("s"); gw=InboundEventGateway(s,bus)
    try: await bootstrap(message_bus=bus, inbound_gateways=[gw,gw])
    except RuntimeError as e: print("  raised", e, "log", log)

async def case_twice():
    print("--- E: bootstrap twice on same gateways")
    log.clear(); bus=MessageBus(); s=Sub("s"); gw=InboundEventGateway(s,bus)
    a1=await bootstrap(message_bus=bus, inbound_gateways=[gw]); a2=await bootstrap(message_bus=bus, inbound_gateways=[gw])
    print("  starts", s.starts, "same list object?", a1.inbound_gateways is a2.inbound_gateways)
    await a1.shutdown(); print("  after a1.shutdown: s.stopped", s.stopped, "a2 still lists it", a2.inbound_gateways==[gw])

async def case_payload():
    print("--- D: notes carry types only")
    log.clear(); bus=MessageBus()
    class Leaky(Exception): pass
    s1=Sub("s1", stop_raise=Leaky("patient: synthetic name")); s2=Sub("s2", stop_raise=ValueError("synthetic 42"))
    app=await bootstrap(message_bus=bus, inbound_gateways=[InboundEventGateway(s1,bus),InboundEventGateway(s2,bus)])
    try: await app.shutdown()
    except Exception as e: print("  raised", type(e).__name__, "notes", e.__notes__)
    # same exception instance raised twice
    shared=RuntimeError("shared")
    s3=Sub("s3", stop_raise=shared); s4=Sub("s4", stop_raise=shared)
    app=await bootstrap(message_bus=bus, inbound_gateways=[InboundEventGateway(s3,bus),InboundEventGateway(s4,bus)])
    try: await app.shutdown()
    except Exception as e: print("  shared instance notes", e.__notes__)
    # shutdown twice accumulates notes on same exception?
    try: await app.shutdown()
    except Exception as e: print("  second shutdown notes", e.__notes__)

async def case_exc_cleanup_cancel():
    print("--- G: real cancel during Exception-path cleanup")
    log.clear(); bus=MessageBus(); rel=asyncio.Event()
    s1=Sub("s1", stop_block=rel); s2=Sub("s2"); s3=Sub("s3", start_raise=ConnectionError("x"))
    gws=[InboundEventGateway(s,bus) for s in (s1,s2,s3)]
    t=asyncio.ensure_future(bootstrap(message_bus=bus, inbound_gateways=gws))
    await s1.stop_entered.wait(); t.cancel()
    try: await t
    except BaseException as e: print("  raised", type(e).__name__, "context", type(e.__context__).__name__, "log", log, "s2 stopped", s2.stopped)

async def case_mock():
    print("--- H: MagicMock / duck-typed gateway")
    from unittest.mock import MagicMock, AsyncMock
    m=MagicMock(); m.start=AsyncMock(); m.stop=AsyncMock()
    try: await bootstrap(message_bus=MessageBus(), inbound_gateways=[m]); print("  accepted")
    except Exception as e: print("  ", type(e).__name__, e)
    class Duck:
        async def start(self): pass
        async def stop(self): pass
    try: await bootstrap(message_bus=MessageBus(), inbound_gateways=[Duck()]); print("  accepted duck")
    except Exception as e: print("  duck:", type(e).__name__, e)

async def main():
    for c in (case_second_cancel, case_timeout_hang, case_timeout_ok, case_taskgroup, case_dup, case_dup_fail, case_twice, case_payload, case_exc_cleanup_cancel, case_mock):
        await c()
asyncio.run(main())
gc.collect()
