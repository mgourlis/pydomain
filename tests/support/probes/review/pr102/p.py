from typing import Any, ClassVar
from pydomain.ddd.domain_event import DomainEvent
from pydomain.es.upcasting import EventUpcaster, UpcasterRegistry
from pydomain.infrastructure.event_registry import EventRegistry
class E(DomainEvent):
    event_version: int = 2
    x: int = 0
class Up(EventUpcaster):
    source_type: ClassVar[str] = "E"; source_version: ClassVar[int] = 1; target_version: ClassVar[int] = 2
    def _transform(self, e): return e
class Bad(EventUpcaster):
    source_type: ClassVar[str] = "F"; source_version: ClassVar[int] = 1; target_version: ClassVar[int] = 2
    def _transform(self, e): return None
class F(DomainEvent):
    event_version: int = 2
u = UpcasterRegistry(); u.register(Up); u.register(Bad)
r = EventRegistry(upcaster_registry=u); r.register(E); r.register(F)
for v in ["2", "1", None, True]:
    try: ev = r.deserialize({"type": "E", "version": v, "data": {"x": 1}}); print(repr(v), "->", type(ev).__name__, ev.event_version)
    except Exception as exc: print(repr(v), "->", type(exc).__name__, exc)
try: r.deserialize({"type": "F", "version": 1, "data": {}})
except Exception as exc: print("non-dict ->", type(exc).__name__, str(exc)[:80])
