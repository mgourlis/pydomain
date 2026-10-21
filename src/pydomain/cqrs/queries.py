from __future__ import annotations

from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pydomain.ddd.id_generator import IdGenerator, Uuid7Generator


class QueryResult(BaseModel):
    """Abstract base for query results.

    Each concrete ``Query`` declares the exact result type its handler
    will produce, making ``dispatch()`` return type explicit and safe.
    """

    model_config = ConfigDict(frozen=True)


class Query[TResult: QueryResult](BaseModel):
    """Base class for queries with generic result type binding.

    A ``Query`` asks a question — "give me this data." Named in
    nominative/descriptive mood. Carries all parameters the handler
    needs. Queries are read-only: no side effects, no aggregate mutation.

    Usage::

        class GetOrder(Query[GetOrderResult]):
            order_id: UUID

    When the model is validated, a missing ``query_id`` comes from
    ``cls._id_generator``: a subclass that sets its own ``_id_generator``
    class variable uses it, every other subclass uses the one set by
    :meth:`configure` (ADR-070). ``model_construct()`` skips validation,
    so its ``query_id`` comes from the base class's generator.
    """

    _id_generator: ClassVar[IdGenerator[UUID]] = Uuid7Generator()

    query_id: UUID = Field(default_factory=lambda: Query._id_generator.generate())

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _ensure_query_id(cls, data: Any) -> Any:
        if isinstance(data, dict) and "query_id" not in data:
            return {**data, "query_id": cls._id_generator.generate()}  # pyright: ignore[reportUnknownVariableType]
        return data

    @classmethod
    def configure(cls, *, id_generator: IdGenerator[UUID]) -> None:
        """Set the system-wide ID generator for Queries.

        Call once at application startup. Affects all ``Query``
        subclasses that use auto-generated query IDs, unless the subclass
        sets its own ``_id_generator``.
        """
        Query._id_generator = id_generator
