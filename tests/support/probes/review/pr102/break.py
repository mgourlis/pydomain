from typing import Any, ClassVar
from pydomain.ddd.domain_event import DomainEvent
from pydomain.es.upcasting import EventUpcaster, UpcasterRegistry
from pydomain.infrastructure import EventRegistry

class Box(DomainEvent):
    event_version: int = 2
    n: int

class BoxV1ToV2(EventUpcaster):
    source_type: ClassVar[str] = "Box"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2
    def _transform(self, e: dict[str, Any]) -> dict[str, Any]:
        e["n"] = e["n"] * 10
        return e

ur = UpcasterRegistry(); ur.register(BoxV1ToV2)
reg = EventRegistry(upcaster_registry=ur); reg.register(Box)

def tryit(label, env):
    try:
        r = reg.deserialize(env)
        print(label, "->", type(r).__name__, getattr(r, "n", None), getattr(r, "event_version", None))
    except Exception as exc:
        print(label, "-> RAISES", type(exc).__name__, str(exc)[:120])

tryit("version None", {"type": "Box", "version": None, "data": {"n": 5}})
tryit("version '2'", {"type": "Box", "version": "2", "data": {"n": 5}})
tryit("version '1'", {"type": "Box", "version": "1", "data": {"n": 5}})
tryit("version 2.0", {"type": "Box", "version": 2.0, "data": {"n": 5}})
tryit("no version key, payload ev=2", {"type": "Box", "data": {"n": 5, "event_version": 2}})
tryit("newer, no ev in payload", {"type": "Box", "version": 3, "data": {"n": 5}})
tryit("version True", {"type": "Box", "version": True, "data": {"n": 5}})
env = {"type": "Box", "version": 1, "data": {"n": 5}}
r = reg.deserialize(env); print("input untouched:", env)
# upcaster forgetting return
class NoneRet(EventUpcaster):
    source_type: ClassVar[str] = "Box2"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2
    def _transform(self, e):
        e["n"] = 1
class Box2(DomainEvent):
    event_version: int = 2
    n: int
ur.register(NoneRet); reg.register(Box2)
tryit("upcaster returns None", {"type": "Box2", "version": 1, "data": {"n": 5}})
# default_factory event_version
from pydantic import Field
class Box3(DomainEvent):
    event_version: int = Field(default_factory=lambda: 2)
    n: int
reg.register(Box3)
tryit("default_factory ev", {"type": "Box3", "version": 2, "data": {"n": 5}})
# bool default
class Box4(DomainEvent):
    event_version: bool = True  # type: ignore
    n: int
reg.register(Box4)
tryit("bool ev default", {"type": "Box4", "version": 1, "data": {"n": 5}})
