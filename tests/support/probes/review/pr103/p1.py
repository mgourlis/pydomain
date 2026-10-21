from typing import Any, ClassVar
from uuid import UUID, uuid4
from pydantic import field_validator, model_validator
from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.entity import Entity
from pydomain.ddd.id_generator import IdGenerator
from pydomain.ddd.specification import Specification
from pydomain.ddd.exceptions import DomainError

FIXED = UUID("00000000-0000-4000-8000-000000000001")
class Fixed:
    def generate(self): return FIXED

class E(DomainEvent):
    _id_generator: ClassVar[IdGenerator[UUID]] = Fixed()
    x: int = 0
print("construct:", E(x=1).event_id == FIXED, "model_construct:", E.model_construct(x=1).event_id == FIXED)

calls = []
class V(DomainEvent):
    items: list[int] = []
    @model_validator(mode="after")
    def after(self):
        calls.append("after"); return self
    @model_validator(mode="before")
    @classmethod
    def before(cls, d: Any):
        calls.append(("before", type(d).__name__)); return d
    @field_validator("correlation_id")
    @classmethod
    def fv(cls, v):
        calls.append("field"); return v
v = V(items=[1])
calls.clear()
s = v.stamp(correlation_id=uuid4(), causation_id=uuid4())
print("stamp validator calls:", calls)
print("event_id same:", s.event_id == v.event_id, "occurred same", s.occurred_at == v.occurred_at)
s.items.append(2); print("orig items", v.items)

class Leaf(Specification):
    threshold: int
    def is_satisfied_by(self, o): return True
for depth in (100, 200, 300, 500, 1000):
    r = Leaf(threshold=1)
    for _ in range(depth): r = r.and_(Leaf(threshold=2))
    try:
        r.model_dump_json(); print(depth, "dump ok")
    except Exception as e: print(depth, type(e).__name__)
    print(depth, "eval", r.is_satisfied_by(None))

class Premium(Specification):
    def is_satisfied_by(self, o): return True
class HV(Specification):
    threshold: int
    def is_satisfied_by(self, o): return True
print(Premium().and_(HV(threshold=1000)).model_dump_json())

class Product(Entity[int]):
    name: str
try: Product(name="W")
except DomainError as e: print("msg:", e)
