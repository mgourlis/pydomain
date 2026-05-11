from __future__ import annotations

from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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
    """

    _id_generator: ClassVar[IdGenerator] = Uuid7Generator()

    query_id: UUID = Field(default_factory=lambda: Query._id_generator.generate())

    model_config = ConfigDict(frozen=True, extra="forbid")

    @classmethod
    def configure(cls, *, id_generator: IdGenerator) -> None:
        """Set the system-wide ID generator for Queries.

        Call once at application startup. Affects all ``Query``
        subclasses that use auto-generated query IDs.
        """
        Query._id_generator = id_generator
