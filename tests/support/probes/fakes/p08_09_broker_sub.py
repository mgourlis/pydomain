"""FAKE-08 / FAKE-09 probes. Synthetic."""
import asyncio
from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.infrastructure.message_broker import MessageBroker
from pydomain.infrastructure.message_subscriber import MessageSubscriber
from pydomain.testing import InMemoryMessageBroker, InMemoryMessageSubscriber

class ThingIE(IntegrationEvent):
    n: int
    tags: list[str] = []

async def f08():
    b = InMemoryMessageBroker()
    print("08 isinstance MessageBroker:", isinstance(b, MessageBroker))
    ev = ThingIE(n=1, tags=["a"])
    await b.publish("t", ev)
    print("08 publish before start accepted:", len(b.published))
    await b.start(); await b.stop()
    await b.publish("t", ev, headers={"traceparent": "x"}, unknown_kw=1)
    print("08 publish after stop accepted:", len(b.published), "headers:", b.published[-1][2])
    print("08 stored by reference:", b.published[0][1] is ev)
    hdr = {"h": "1"}; await b.publish("t", ev, headers=hdr); hdr["h"] = "mutated"
    print("08 headers by reference:", b.published[-1][2])
    try:
        b.published[0][1].tags.append("mutated-after-publish")
        print("08 event mutable after publish (frozen?):", b.published[0][1].tags)
    except Exception as e:
        print("08 event frozen:", type(e).__name__)

async def f09():
    s = InMemoryMessageSubscriber()
    print("09 isinstance MessageSubscriber:", isinstance(s, MessageSubscriber))
    try:
        r = await s.simulate_message("nobody", {"x": 1})
        print("09 unknown topic: returned", r, "silently (docstring promises KeyError)")
    except KeyError as e:
        print("09 unknown topic KeyError", e)
    got = []
    async def h1(p): got.append(("h1", p))
    async def h2(p): got.append(("h2", p))
    s.subscribe("t", h1); s.subscribe("t", h2)
    await s.simulate_message("t", {"x": 1})
    print("09 two subscribes same topic -> delivered to:", [g[0] for g in got])
    print("09 started before start():", s.started)
    await s.stop()
    await s.simulate_message("t", {"x": 2})
    print("09 delivery after stop (never started):", len(got), "started", s.started, "stopped", s.stopped)
    await s.start()
    print("09 start after stop: started", s.started, "stopped", s.stopped)
    payload = {"nested": {"k": [1]}}
    async def mut(p): p["nested"]["k"].append(2)
    s.subscribe("m", mut); await s.simulate_message("m", payload)
    print("09 payload passed by reference (no JSON round trip):", payload)
    from datetime import datetime
    async def ok(p): got.append(p)
    s.subscribe("dt", ok); await s.simulate_message("dt", {"when": datetime(2020,1,1)})
    print("09 non-JSON payload accepted:", type(got[-1]["when"]).__name__)
    async def boom(p): raise RuntimeError("handler fails")
    s.subscribe("b", boom)
    try: await s.simulate_message("b", {})
    except RuntimeError as e: print("09 handler exception propagates to caller (no nack/redelivery):", e)

asyncio.run(f08()); asyncio.run(f09())
