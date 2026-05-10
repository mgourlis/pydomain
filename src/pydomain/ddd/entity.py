from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from pydomain.ddd.id_generator import IdGenerator, Uuid7Generator


class Entity[TId](BaseModel):
    """Generic Entity base class.

    Entities are distinguished by their **identity** (the ``id`` field),
    not their attributes. Two entities are equal if they share the same
    type and ``id``, regardless of other field values.

    The ``TId`` type parameter determines the identity type — ``UUID``,
    ``int``, ``str``, or any hashable, serializable type.

    For ``Entity[UUID]`` subclasses the ``id`` is auto-generated via the
    system-wide ``IdGenerator`` when omitted. For other types, ``id``
    must always be provided explicitly.
    """

    _id_generator: ClassVar[IdGenerator] = Uuid7Generator()
    id: TId
    version: int = 0

    model_config = ConfigDict(frozen=False)

    @model_validator(mode="before")
    @classmethod
    def _ensure_id(cls, data: Any) -> Any:
        if isinstance(data, dict) and "id" not in data:
            id_field = cls.__pydantic_fields__.get("id")
            if id_field is not None and id_field.annotation is UUID:
                data["id"] = cls._id_generator.generate()
        return data

    @classmethod
    def configure(cls, *, id_generator: IdGenerator) -> None:
        """Set the system-wide ID generator.

        Call once at application startup. Affects all ``Entity[UUID]``
        subclasses that use auto-generated IDs.
        """
        Entity._id_generator = id_generator

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Entity):
            return False
        return type(self) is type(other) and self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)
