from typing import Protocol, runtime_checkable
from uuid import UUID

from uuid_utils import uuid7


@runtime_checkable
class IdGenerator(Protocol):
    def generate(self) -> UUID: ...


class Uuid7Generator:
    def generate(self) -> UUID:
        return UUID(int=uuid7().int)
