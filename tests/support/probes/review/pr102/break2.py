from typing import Any, ClassVar
from pydantic import Field
from pydomain.ddd.domain_event import DomainEvent
from pydomain.es.upcasting import EventUpcaster, UpcasterRegistry
from pydomain.infrastructure import EventRegistry
class A(DomainEvent):
    event_version: int = Field(default_factory=lambda data: 2)
    n: int
class B(DomainEvent):
    event_version: int = 2
    n: int
class BOverride(EventUpcaster):
    source_type: ClassVar[str] = "B"; source_version: ClassVar[int] = 1; target_version: ClassVar[int] = 2
    def upcast(self, e): return None
ur = UpcasterRegistry(); ur.register(BOverride)
reg = EventRegistry(upcaster_registry=ur); reg.register(A); reg.register(B)
for label, env in [("factory(data)", {"type": "A", "version": 2, "data": {"n": 1}}),
                   ("float 2.0", {"type": "B", "version": 2.0, "data": {"n": 1}}),
                   ("upcast() override returns None", {"type": "B", "version": 1, "data": {"n": 1}}),
                   ("newer w/ ev in payload", {"type": "B", "version": 5, "data": {"n": 1, "event_version": 4}})]:
    try:
        r = reg.deserialize(env); print(label, "->", r.event_version)
    except Exception as exc:
        print(label, "-> RAISES", type(exc).__name__, str(exc)[:110])
