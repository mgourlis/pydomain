import sys
from typing import Any
from pydomain.ddd.specification import Specification, AndSpecification, OrSpecification, NotSpecification

class Gt(Specification):
    n: int
    def is_satisfied_by(self, obj):
        calls.append(self.n); return obj > self.n
    def subsumes(self, other): return isinstance(other, Gt) and other.n >= self.n
calls = []

class LoggingAnd(AndSpecification):
    def is_satisfied_by(self, obj: Any) -> bool:
        return super().is_satisfied_by(obj)
class LoggingNot(NotSpecification):
    def is_satisfied_by(self, obj: Any) -> bool:
        return super().is_satisfied_by(obj)
class SubsAnd(AndSpecification):
    def subsumes(self, other):
        return super().subsumes(other)

sys.setrecursionlimit(200)
for name, spec, op in [
    ("LoggingAnd super()", LoggingAnd(specifications=(Gt(n=1), Gt(n=2))), lambda s: s.is_satisfied_by(5)),
    ("LoggingNot super()", LoggingNot(specification=Gt(n=1)), lambda s: s.is_satisfied_by(5)),
    ("SubsAnd super()", SubsAnd(specifications=(Gt(n=1),)), lambda s: s.subsumes(Gt(n=3))),
    ("plain And nested in LoggingAnd", AndSpecification(specifications=(LoggingAnd(specifications=(Gt(n=1),)),)), lambda s: s.is_satisfied_by(5)),
]:
    try: print(name, op(spec))
    except RecursionError: print(name, "RecursionError")
sys.setrecursionlimit(1000)
# short-circuit order
calls.clear()
s = Gt(n=1).and_(Gt(n=10).or_(Gt(n=2))).and_(Gt(n=3).not_()).or_(Gt(n=4))
print("result", s.is_satisfied_by(5), "calls", calls)
# reference recursive evaluation
def ref(sp, o):
    if type(sp) is AndSpecification: return all(ref(c,o) for c in sp.specifications)
    if type(sp) is OrSpecification: return any(ref(c,o) for c in sp.specifications)
    if type(sp) is NotSpecification: return not ref(sp.specification, o)
    return sp.is_satisfied_by(o)
calls.clear(); ref(s,5); print("ref calls", calls)
# deep chain
d = Gt(n=0)
for i in range(5000): d = d.and_(Gt(n=0)) if i%2 else d.or_(Gt(n=0)).not_()
print("deep", d.is_satisfied_by(1))
# dump
print(Gt(n=1).and_(Gt(n=2).not_()).model_dump())
print(Gt(n=1).and_(Gt(n=2)).model_dump_json())
# hashing / eq unchanged
print(hash(Gt(n=1).and_(Gt(n=2))) == hash(Gt(n=1).and_(Gt(n=2))))
# empty via model_construct
try: print("empty And", AndSpecification.model_construct(specifications=()).is_satisfied_by(1))
except BaseException as ex: print("empty And", type(ex).__name__)
# non-bool leaf
class Truthy(Specification):
    def is_satisfied_by(self, obj): return [1]
print("non-bool", Truthy().and_(Truthy()).is_satisfied_by(0), Truthy().not_().is_satisfied_by(0))
