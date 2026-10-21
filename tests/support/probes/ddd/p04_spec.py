"""DDD-14, DDD-15, DDD-21 probes, plus the concepts/specifications.md example."""
import sys
from typing import Any

from pydomain.ddd import AndSpecification, NotSpecification, Specification


def attempt(label, fn):
    try:
        r = fn()
        print(f"{label}: OK -> {r!r}")
        return r
    except Exception as e:  # noqa: BLE001
        print(f"{label}: {type(e).__name__}: {str(e).splitlines()[0][:150]}")
        return None


class MinTotal(Specification):
    threshold: int

    def is_satisfied_by(self, obj: Any) -> bool:
        return obj >= self.threshold

    def subsumes(self, other: Specification) -> bool:
        return isinstance(other, MinTotal) and self.threshold <= other.threshold


class IsEven(Specification):
    def is_satisfied_by(self, obj: Any) -> bool:
        return obj % 2 == 0


print("--- DDD-14 ---")
a, b = MinTotal(threshold=100), IsEven()
comp = a.and_(b)
dump = attempt("And.model_dump()", lambda: comp.model_dump())
attempt("Not.model_dump()", lambda: a.not_().model_dump())
attempt("And.model_dump(serialize_as_any=True)", lambda: comp.model_dump(serialize_as_any=True))
attempt("leaf model_dump", lambda: a.model_dump())
attempt("AndSpecification.model_validate(dump)", lambda: AndSpecification.model_validate(dump))
attempt("And.model_dump_json()", lambda: comp.model_dump_json())
attempt("hash(comp)", lambda: hash(comp) is not None)
attempt("comp == a.and_(b)", lambda: comp == a.and_(b))
attempt("comp == a.and_(IsEven())", lambda: comp == MinTotal(threshold=100).and_(IsEven()))

print("--- DDD-15 ---")
print("leaf reflexive a.subsumes(a):", a.subsumes(a))
print("IsEven reflexive:", b.subsumes(b))
print("And reflexive comp.subsumes(comp):", comp.subsumes(comp))
print("a.subsumes(a.and_(b)) (true in set terms):", a.subsumes(comp))
big = MinTotal(threshold=50)
print("And(MinTotal50, MinTotal100).subsumes(MinTotal200):", big.and_(a).subsumes(MinTotal(threshold=200)))
print("Or(MinTotal100, IsEven).subsumes(MinTotal200):", a.or_(b).subsumes(MinTotal(threshold=200)))
print("Not(...).subsumes(Not(...)):", NotSpecification(specification=a).subsumes(NotSpecification(specification=a)))
# soundness check over a domain: whenever x.subsumes(y), sat(y) subset of sat(x)
specs = [MinTotal(threshold=t) for t in (0, 50, 100)] + [IsEven()]
specs += [s.and_(t) for s in specs for t in specs] + [s.or_(t) for s in specs for t in specs] + [s.not_() for s in specs]
violations = 0
for x in specs:
    for y in specs:
        if x.subsumes(y):
            if any(y.is_satisfied_by(n) and not x.is_satisfied_by(n) for n in range(-5, 210)):
                violations += 1
print("soundness violations over", len(specs) ** 2, "pairs:", violations)
spec = IsEven()
for _ in range(sys.getrecursionlimit()):
    spec = spec.and_(IsEven())
attempt(f"deep and_ chain depth {sys.getrecursionlimit()} is_satisfied_by", lambda: spec.is_satisfied_by(2))
attempt("deep chain subsumes", lambda: spec.subsumes(IsEven()))
s2 = IsEven()
for _ in range(300):
    s2 = s2.and_(IsEven())
attempt("depth 300 is_satisfied_by", lambda: s2.is_satisfied_by(2))

print("--- concepts/specifications.md:70-84 example ---")


class IsAdult(Specification):
    def is_satisfied_by(self, person: Any) -> bool:
        return person >= 18


class IsSenior(Specification):
    def is_satisfied_by(self, person: Any) -> bool:
        return person >= 65


print("doc asserts adult.subsumes(senior) is True; actual:", IsAdult().subsumes(IsSenior()))

print("--- DDD-21 ---")
attempt("Specification[int]", lambda: Specification[int])
attempt("AndSpecification[int]", lambda: AndSpecification[int])
print("is_satisfied_by annotation:", Specification.is_satisfied_by.__annotations__)
attempt("mixed-type composition IsEven().and_(MinTotal) on str", lambda: a.and_(b).is_satisfied_by("x"))
