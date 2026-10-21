import uuid
from pydantic import ConfigDict
from pydantic.alias_generators import to_camel
from pydomain.ddd.domain_event import DomainEvent
from pydomain.cqrs.commands import Command, CommandResult
class E(DomainEvent):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)
x = uuid.uuid4()
print("alias explicit kept:", E.model_validate({"eventId": str(x)}).event_id == x)
print("fields_set dev-vs-branch:", sorted(DomainEvent().model_fields_set))
class C(Command[CommandResult]): pass
print("cmd fields_set:", sorted(C().model_fields_set), C().model_dump(exclude_unset=True).keys())
