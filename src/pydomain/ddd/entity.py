from __future__ import annotations

from types import UnionType
from typing import (
    Annotated,
    Any,
    ClassVar,
    NewType,
    TypeAliasType,
    TypeVar,
    Union,
    get_args,
    get_origin,
)

from pydantic import BaseModel, ConfigDict, model_validator

from pydomain.ddd.exceptions import DomainError
from pydomain.ddd.id_generator import IdGenerator, Uuid7Generator


class _UnboundTypeVar(TypeError):
    """An id annotation holds a ``TypeVar``: the generic was not parametrized."""


def _runtime_types(annotation: Any) -> tuple[type, ...] | None:
    """Resolve an id annotation to the runtime types ``isinstance`` can check.

    Follows ``NewType`` and PEP 695 aliases, strips ``Annotated``, and
    flattens unions, at any depth: ``Optional[X]`` with ``type X = Y | int``
    and ``type Y = UUID`` resolves to ``(UUID, int, NoneType)``. A
    parametrised generic resolves to its origin (``tuple[int, int]`` to
    ``tuple``). Returns ``None`` for ``Any``, which every value satisfies.
    Raises :class:`_UnboundTypeVar` for an unbound ``TypeVar`` and
    ``TypeError`` for any other type it cannot check, such as
    ``Literal["a"]`` (ADR-070).
    """
    pending = [annotation]
    found: list[type] = []
    while pending:
        current = pending.pop()
        origin = get_origin(current)
        if isinstance(current, NewType):
            pending.append(current.__supertype__)
        elif isinstance(current, TypeAliasType):
            pending.append(current.__value__)
        elif current is Any:
            return None
        elif origin is Annotated:
            pending.append(get_args(current)[0])
        elif origin is Union or origin is UnionType:
            pending.extend(get_args(current))
        elif isinstance(origin, type):
            found.append(origin)
        elif isinstance(current, type):
            found.append(current)
        else:
            raise _UnboundTypeVar if isinstance(current, TypeVar) else TypeError
    return tuple(found)


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
    does not, a :class:`DomainError` is raised. ``NewType`` ids, PEP 695
    aliases (``type OrderId = UUID``), ``Annotated`` and unions are
    resolved, at any depth, to the types they name; ``Any`` accepts every
    value. An id type that cannot be checked raises :class:`DomainError`
    too: for the unbound ``TypeVar`` of a generic entity built without a
    type argument, it says to parametrize the generic or pass an id; for
    any other type, such as ``Literal["a"]``, it asks for an explicit id.
    The guard never writes into the data it is given.

    Set a generator for one entity class and its subclasses with
    ``Sub.configure()`` or an ``_id_generator`` class variable, or the
    default for all entities with ``Entity.configure()``; the nearest
    class's own generator wins. The default generator is
    :class:`Uuid7Generator`.
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
                expected = id_field.annotation
                try:
                    check_types = _runtime_types(expected)
                except _UnboundTypeVar:
                    msg = (
                        f"{cls.__name__} cannot check a generated id against "
                        f"{expected!r}: parametrize the generic (for example "
                        f"{cls.__name__}[UUID]) or pass an id"
                    )
                    raise DomainError(msg) from None
                except TypeError:
                    msg = (
                        f"{cls.__name__} cannot check a generated id against "
                        f"{expected!r}; pass an explicit id"
                    )
                    raise DomainError(msg) from None
                generated = cls._id_generator.generate()
                if check_types is not None and not isinstance(generated, check_types):
                    msg = (
                        f"{type(cls._id_generator).__name__} produced "
                        f"{type(generated).__name__}, but "
                        f"{cls.__name__} expects {expected!r}"
                    )
                    raise DomainError(msg)
                data = {**data, "id": generated}
        return data  # pyright: ignore[reportUnknownVariableType]

    @classmethod
    def configure(cls, *, id_generator: IdGenerator[Any]) -> None:
        """Set the ID generator of this class and its subclasses.

        ``Entity.configure(...)`` sets the default for every subclass
        without its own generator; call it once at application startup.
        Called on a subclass, it sets the generator of that subclass and
        its subclasses only, exactly as declaring ``_id_generator`` on it
        does. A class with its own generator, declared or configured, keeps
        it when a base class is configured later (ADR-078).
        """
        cls._id_generator = id_generator

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Entity):
            return False
        return type(self) is type(other) and self.id == other.id  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType,reportUnknownVariableType]

    def __hash__(self) -> int:
        return hash(self.id)
