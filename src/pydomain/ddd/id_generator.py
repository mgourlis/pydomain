from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from uuid_utils import uuid7


@runtime_checkable
class IdGenerator[TId](Protocol):
    """Protocol for ID generators, parameterized by the ID type they produce.

    Implementations must provide a ``generate()`` method that returns a
    value of type ``TId``.

    Example concrete implementations::

        class Uuid7Generator:
            def generate(self) -> UUID: ...

        class SnowflakeIdGenerator:
            def generate(self) -> int: ...
    """

    def generate(self) -> TId: ...


class Uuid7Generator:
    """Generates UUIDv7 identifiers.

    Structurally conforms to ``IdGenerator[UUID]``.
    """

    def generate(self) -> UUID:
        return UUID(int=uuid7().int)
