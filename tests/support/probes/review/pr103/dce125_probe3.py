import json
from typing import Annotated, Any, ClassVar, Optional
from uuid import UUID, uuid4

from pydantic import TypeAdapter, model_validator
from pydantic.types import UuidVersion

from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.id_generator import IdGenerator

FIXED = UUID("00000000-0000-4000-8000-0000000000e1")


class Fixed:
    def generate(self) -> UUID:
        return FIXED


class Shipped(DomainEvent):
    _id_generator: ClassVar[IdGenerator[UUID]] = Fixed()
    n: int = 0


print("json:", Shipped.model_validate_json(json.dumps({"n": 1})).event_id == FIXED)
print("construct:", Shipped.model_construct(n=1).event_id == FIXED)
print("fields_set:", Shipped(n=1).model_fields_set)

calls = []


class Counted(DomainEvent):
    @model_validator(mode="before")
    @classmethod
    def _b(cls, d: Any) -> Any:
        calls.append("before")
        return d

    @model_validator(mode="after")
    def _a(self) -> "Counted":
        calls.append("after")
        return self


c = Counted()
calls.clear()
c.stamp(correlation_id=uuid4(), causation_id=uuid4())
print("validators during stamp:", calls)

info = DomainEvent.model_fields["correlation_id"]
print("annotation:", info.annotation, "metadata:", info.metadata)


class V7(DomainEvent):
    correlation_id: Annotated[UUID, UuidVersion(7)] | None = None


i7 = V7.model_fields["correlation_id"]
ta = TypeAdapter(Annotated[i7.annotation, *i7.metadata])
try:
    ta.validate_python(uuid4())
    print("v7 constraint NOT enforced")
except Exception as e:  # noqa: BLE001
    print("v7 enforced:", type(e).__name__)


type Y = UUID
type X = Y | int
print(Optional[X], (Annotated[UUID, "m"] | None))
