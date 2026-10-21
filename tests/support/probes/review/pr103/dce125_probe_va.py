from uuid import UUID, uuid4

from pydantic import BaseModel, SerializeAsAny

from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.specification import Specification

e = DomainEvent()
c = e.model_copy(deep=True)
v = type(e).__pydantic_validator__
try:
    v.validate_assignment(c, "correlation_id", str(uuid4()))
    print("assigned", type(c.correlation_id), c.correlation_id)
except Exception as exc:  # noqa: BLE001
    print("validate_assignment:", type(exc).__name__, exc)
try:
    v.validate_assignment(c, "causation_id", 123)
except Exception as exc:  # noqa: BLE001
    print("bad:", type(exc).__name__, str(exc).splitlines()[:3])
print("original untouched:", e.correlation_id)


class Leaf(Specification):
    threshold: int

    def is_satisfied_by(self, obj):
        return obj > self.threshold


class Holder(BaseModel):
    specs: tuple[SerializeAsAny[Specification], ...]


print(Holder(specs=(Leaf(threshold=3),)).model_dump())
