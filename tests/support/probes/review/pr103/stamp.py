import uuid
from uuid import UUID
from pydantic import field_validator, model_validator, Field
from pydomain.ddd.domain_event import DomainEvent
calls=[]
class E(DomainEvent):
    items: list[int] = []
    @model_validator(mode="after")
    def a(self): calls.append("after"); return self
    @field_validator("correlation_id")
    @classmethod
    def fv(cls, v): calls.append("field"); return v
e = E(items=[1]); calls.clear()
s = e.stamp(correlation_id=str(uuid.uuid4()), causation_id=uuid.uuid4())
print("calls during stamp", calls, type(s.correlation_id).__name__, s.items is e.items, s.event_id==e.event_id, sorted(s.model_fields_set))
for bad in ["x", 123]:
    try: e.stamp(correlation_id=bad, causation_id=uuid.uuid4())
    except Exception as ex: print("bad", bad, type(ex).__name__)
class Narrow(DomainEvent):
    correlation_id: UUID = Field(default_factory=uuid.uuid4)
try: Narrow().stamp(correlation_id=None, causation_id=uuid.uuid4()); print("narrow None accepted")
except Exception as ex: print("narrow None", type(ex).__name__)
print("None on base:", DomainEvent().stamp(correlation_id=None, causation_id=None).correlation_id)
from pydomain.ddd import domain_event as m
print("cache", m._id_adapter.cache_info())
