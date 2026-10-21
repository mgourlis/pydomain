"""Probe: strict-JSON rejection messages, stamp() validation, registry checks, to_entry ids."""
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry
from pydomain.ddd.domain_event import DomainEvent

SECRET = "SENTINEL-VALUE-123"


class E(IntegrationEvent):
    data: Any = None


def show(label, **kw):
    try:
        E(**kw); print(label, "ACCEPTED")
    except ValidationError as e:
        msg = e.errors()[0]["msg"]
        print(label, "|", type(e).__name__, "|", msg.split(". IntegrationEvent")[0], "| leaks" if SECRET in str(e) else "")

show("nested datetime", data={"at": datetime.now(UTC)})
show("deep list", data={"a": [1, {"b": (SECRET,)}]})
show("tuple top", data=(SECRET,))
show("set top", data={SECRET})
show("int key", data={1: SECRET})
show("nan", data={"x": float("nan")})
show("inf in list", data=[float("inf")])
show("ok", data={"a": [1, 2.5, None, True, {"b": "c"}]})
show("bytes", data={"k": SECRET.encode()})
show("tricky key repr", data={SECRET: object()})  # key is part of the path!


class Typed(IntegrationEvent):
    items: list[int]
try:
    print("list[int] given tuple:", Typed(items=(1, 2)).items)
except ValidationError as e:
    print("list[int] given tuple refused")

# stamp
ev = E(data=1)
for c in (uuid4(), 5, None):
    try:
        r = ev.stamp(correlation_id=c, causation_id="k"); print("stamp", type(c).__name__, "accepted ->", r.correlation_id)
    except ValidationError as e:
        print("stamp", type(c).__name__, "-> ValidationError", e.errors()[0]["type"])

# registry
class D1(DomainEvent):
    n: int = 0
class D2(DomainEvent):
    n: int = 0
class A(IntegrationEvent):
    n: int
class B(IntegrationEvent):
    n: int
class ASub(A):
    extra: str = "x"

r = OutboundEventRegistry()
r.register_translation(D1, A, "t", lambda e: A(n=e.n))
r.register_translation(D2, A, "t", lambda e: A(n=e.n)); print("reg: two types, same class ok")
try: r.register_translation(D2, B, "t", lambda e: B(n=e.n)); print("reg collision ACCEPTED")
except ValueError as e: print("reg collision ->", e)
print("reg after refused: D2 still", r.resolve("t").__name__)
r2 = OutboundEventRegistry()
r2.register_translation(D1, A, "t", lambda e: A(n=e.n))
r2.register_translation(D1, B, "t", lambda e: B(n=e.n)); print("reg hot-swap same type ok ->", r2.resolve("t").__name__)
# stale binding: D1 moves to another topic, then D2 binds the old topic with a new class
r3 = OutboundEventRegistry()
r3.register_translation(D1, A, "t", lambda e: A(n=e.n))
r3.register_translation(D1, A, "t2", lambda e: A(n=e.n))
try: r3.register_translation(D2, B, "t", lambda e: B(n=e.n)); print("reg after D1 moved: accepted")
except ValueError as e: print("reg after D1 moved topic: REFUSED ->", e)

r4 = OutboundEventRegistry()
r4.register_translation(D1, A, "t", lambda e: ASub(n=1))
try: r4.to_entry(D1()); print("subclass ACCEPTED")
except TypeError as e: print("subclass ->", e)
r4.register_translation(D1, A, "t", lambda e: {"n": 1})
try: r4.to_entry(D1()); print("dict ACCEPTED")
except TypeError as e: print("dict ->", e)

r5 = OutboundEventRegistry(); r5.register_translation(D1, A, "t", lambda e: A(n=1))
d = D1()
en = r5.to_entry(d); print("unstamped entry ids:", en.correlation_id, en.causation_id)
c, k = uuid4(), uuid4()
ds = d.stamp(correlation_id=c, causation_id=k)
en = r5.to_entry(ds); print("stamped: corr==", en.correlation_id == str(c), "cause==event_id", en.causation_id == str(ds.event_id), "cause!=command", en.causation_id != str(k))
