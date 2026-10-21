from pydomain.ddd.domain_event import DomainEvent
from pydomain.es.upcasting import UpcasterRegistry
from pydomain.infrastructure import EventRegistry
class B(DomainEvent):
    event_version: int = 2
    n: int
reg = EventRegistry(upcaster_registry=UpcasterRegistry()); reg.register(B)
for label, v in [("007", "002"), ("huge", "1"*5000), ("bytes", b"2"), ("full-width", "２"), ("newer str", "9")]:
    try:
        r = reg.deserialize({"type": "B", "version": v, "data": {"n": 1}}); print(label, "->", r.event_version)
    except Exception as exc:
        print(label, "-> RAISES", type(exc).__name__, str(exc)[:90])
