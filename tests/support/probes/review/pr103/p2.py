from typing import Any, ClassVar, Annotated
from uuid import UUID, uuid4
from pydantic import field_validator, model_validator
from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.entity import Entity
from pydomain.ddd.exceptions import DomainError
from pydomain.ddd.specification import NotSpecification
from pydomain.cqrs.commands import Command, EmptyCommandResult
from pydomain.cqrs.queries import Query, QueryResult
calls=[]
class T(DomainEvent):
    @model_validator(mode="after")
    def both(self):
        calls.append("after")
        if (self.correlation_id is None) != (self.causation_id is None): raise ValueError
        return self
    @field_validator("correlation_id")
    @classmethod
    def fv(cls, v): calls.append("fv"); return v
e=T(); calls.clear()
s=e.stamp(correlation_id=str(uuid4()), causation_id=uuid4()); print("stamp ok", type(s.correlation_id).__name__, calls)
class G[X](Entity[X]): pass
try: G()
except DomainError as x: print("DomainError:", x)
class A(Entity[Any]): pass
print("Any", A().id)
try: NotSpecification.model_construct().is_satisfied_by(1)
except ValueError as x: print("NotErr:", x)
