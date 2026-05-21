from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, model_validator

from pydomain.ddd.exceptions import DomainError
from pydomain.ddd.id_generator import IdGenerator, Uuid7Generator


class Entity[TId](BaseModel):
    """Generic Entity base class.

    Entities are distinguished by their **identity** (the ``id`` field),
    not their attributes. Two entities are equal if they share the same
    type and ``id``, regardless of other field values.

    The ``TId`` type parameter determines the identity type — ``UUID``,
    ``int``, ``str``, or any hashable, serializable type.

    Auto-generation
    ---------------
    When ``id`` is omitted at construction, the entity calls the
    configured ``IdGenerator``. A **runtime type guard** verifies that
    the generated value matches the declared ``TId`` annotation — if it
    does not, a :class:`DomainError` is raised.

    Configure a generator per entity subclass or globally via
    :meth:`configure`. The default generator is :class:`Uuid7Generator`.
    """

    _id_generator: ClassVar[IdGenerator[Any]] = Uuid7Generator()
    id: TId
    version: int = 0

    model_config = ConfigDict(frozen=False)

    @model_validator(mode="before")
    @classmethod
    def _ensure_id(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if "id" not in data:
            id_field = cls.__pydantic_fields__.get("id")
            if id_field is not None:
                generated = cls._id_generator.generate()
                expected = id_field.annotation
                # Unwrap Optional / Union annotations to find the base type
                origin = getattr(expected, "__origin__", None)
                args = getattr(expected, "__args__", ())
                check_type: Any = expected
                if origin is type(None):
                    return data  # pyright: ignore[reportUnknownVariableType]
                if args:
                    check_type = (
                        tuple(a for a in args if a is not type(None)) or expected
                    )
                if check_type is not None and not isinstance(generated, check_type):  # pyright: ignore[reportArgumentType]
                    msg = (
                        f"{type(cls._id_generator).__name__} produced "
                        f"{type(generated).__name__}, but "
                        f"{cls.__name__} expects {expected!r}"
                    )
                    raise DomainError(msg)
                data["id"] = generated
        return data  # pyright: ignore[reportUnknownVariableType]

    @classmethod
    def configure(cls, *, id_generator: IdGenerator[Any]) -> None:
        """Set the system-wide ID generator.

        Call once at application startup. Affects all ``Entity``
        subclasses that use auto-generated IDs unless the subclass
        overrides ``_id_generator``.
        """
        Entity._id_generator = id_generator

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Entity):
            return False
        return type(self) is type(other) and self.id == other.id  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType,reportUnknownVariableType]

    def __hash__(self) -> int:
        return hash(self.id)
