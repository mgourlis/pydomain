from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar
from uuid import UUID

import pytest
from pydantic import BaseModel

from pydomain.cqrs import Command, EmptyCommandResult, Query, QueryResult
from pydomain.ddd import DomainEvent, Entity, IdGenerator, Uuid7Generator


class FakeIdGenerator:
    """Deterministic ID generator for testing."""

    def __init__(self, start: int = 0) -> None:
        self._counter = start

    def generate(self) -> UUID:
        result = UUID(int=self._counter)
        self._counter += 1
        return result


class TestUuid7Generator:
    def test_returns_uuid_instance(self) -> None:
        gen = Uuid7Generator()
        result = gen.generate()
        assert isinstance(result, UUID)

    def test_generates_uuid_version_7(self) -> None:
        gen = Uuid7Generator()
        result = gen.generate()
        # UUID version is encoded in the 4 most significant bits of byte 6 (octet 6)
        # Version 7 means the MSB nibble of byte 6 is 0x7
        version = (result.bytes[6] >> 4) & 0xF
        assert version == 7

    def test_generates_unique_ids(self) -> None:
        gen = Uuid7Generator()
        ids = {gen.generate() for _ in range(100)}
        assert len(ids) == 100

    def test_generates_time_ordered_ids(self) -> None:
        gen = Uuid7Generator()
        ids = [gen.generate() for _ in range(10)]
        # UUIDv7 embeds a millisecond timestamp; consecutive calls
        # should produce monotonically increasing integer values.
        for i in range(len(ids) - 1):
            assert ids[i] < ids[i + 1]


class TestIdGeneratorProtocol:
    def test_uuid7_generator_satisfies_protocol(self) -> None:
        gen = Uuid7Generator()
        assert isinstance(gen, IdGenerator)

    def test_fake_generator_satisfies_protocol(self) -> None:
        gen = FakeIdGenerator()
        assert isinstance(gen, IdGenerator)

    def test_custom_generator_satisfies_protocol(self) -> None:
        class CustomGen:
            def generate(self) -> UUID:
                return UUID(int=42)

        assert isinstance(CustomGen(), IdGenerator)

    def test_protocol_is_not_instantiable(self) -> None:
        # Protocol classes with ... methods can't be instantiated
        # (they have no __init__)
        pass


class TestFakeIdGenerator:
    def test_returns_deterministic_uuids(self) -> None:
        gen = FakeIdGenerator(start=100)
        assert gen.generate() == UUID(int=100)

    def test_increments_on_each_call(self) -> None:
        gen = FakeIdGenerator(start=0)
        assert gen.generate() == UUID(int=0)
        assert gen.generate() == UUID(int=1)
        assert gen.generate() == UUID(int=2)

    def test_reproducible_sequence(self) -> None:
        gen_a = FakeIdGenerator(start=42)
        gen_b = FakeIdGenerator(start=42)
        seq_a = [gen_a.generate() for _ in range(5)]
        seq_b = [gen_b.generate() for _ in range(5)]
        assert seq_a == seq_b

    def test_default_start_is_zero(self) -> None:
        gen = FakeIdGenerator()
        assert gen.generate() == UUID(int=0)

    def test_produces_valid_uuid_instances(self) -> None:
        gen = FakeIdGenerator()
        for _ in range(10):
            assert isinstance(gen.generate(), UUID)


# ---------------------------------------------------------------------------
# configure() is per class (DCE-128, decision log B-101)
# ---------------------------------------------------------------------------


class _Found(QueryResult):
    """Synthetic query result."""


class _FixedId:
    """Returns one id for every call."""

    def __init__(self, value: int) -> None:
        self.value = UUID(int=value)

    def generate(self) -> UUID:
        return self.value


_ROOTS: list[tuple[Any, type[BaseModel], str]] = [
    (Entity, Entity[UUID], "id"),
    (DomainEvent, DomainEvent, "event_id"),
    (Command, Command[EmptyCommandResult], "command_id"),
    (Query, Query[_Found], "query_id"),
]


@pytest.fixture(params=_ROOTS, ids=["Entity", "DomainEvent", "Command", "Query"])
def root(request: pytest.FixtureRequest) -> Iterator[tuple[Any, type[BaseModel], str]]:
    """A base class, the class to subclass and its id field; the base's
    generator is restored afterwards."""
    base, parent, field = request.param
    original = base.__dict__["_id_generator"]
    try:
        yield base, parent, field
    finally:
        base._id_generator = original


def _new_id(cls: type[BaseModel], field: str) -> UUID:
    value = getattr(cls(), field)
    assert isinstance(value, UUID)
    return value


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-128 B-101: Sub.configure() replaces the base class's generator",
)
def test_subclass_configure_sets_only_that_class_and_its_subclasses(
    root: tuple[Any, type[BaseModel], str],
) -> None:
    base, parent, field = root

    class Invoice(parent):  # type: ignore[valid-type, misc]
        pass

    class CreditNote(Invoice):  # type: ignore[valid-type, misc]
        pass

    class Shipment(parent):  # type: ignore[valid-type, misc]
        pass

    Invoice.configure(id_generator=_FixedId(1))

    assert _new_id(Invoice, field) == UUID(int=1)
    assert _new_id(CreditNote, field) == UUID(int=1)
    assert _new_id(Shipment, field) != UUID(int=1)
    assert base._id_generator is not Invoice._id_generator


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-128 B-101: a later base configure() overrides Sub.configure()",
)
def test_base_configure_keeps_a_subclass_override(
    root: tuple[Any, type[BaseModel], str],
) -> None:
    base, parent, field = root

    class Invoice(parent):  # type: ignore[valid-type, misc]
        _id_generator: ClassVar[IdGenerator[UUID]] = _FixedId(1)

    class Receipt(parent):  # type: ignore[valid-type, misc]
        pass

    class Shipment(parent):  # type: ignore[valid-type, misc]
        pass

    Receipt.configure(id_generator=_FixedId(2))
    base.configure(id_generator=_FixedId(3))

    assert _new_id(Invoice, field) == UUID(int=1)
    assert _new_id(Receipt, field) == UUID(int=2)
    assert _new_id(Shipment, field) == UUID(int=3)
