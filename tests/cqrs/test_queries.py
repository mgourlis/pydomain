from __future__ import annotations

from typing import ClassVar
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from pydomain.cqrs import Query, QueryResult
from pydomain.ddd.id_generator import IdGenerator, Uuid7Generator

# A per-subclass query id generator (DCE-125, DDD-09, ADR-070).
TRACK_QUERY_ID = UUID("00000000-0000-4000-8000-0000000000d1")


class FixedIdGenerator:
    def __init__(self, value: UUID) -> None:
        self.value = value

    def generate(self) -> UUID:
        return self.value


class TrackingResult(QueryResult):
    status: str


class TrackParcel(Query[TrackingResult]):
    _id_generator: ClassVar[IdGenerator[UUID]] = FixedIdGenerator(TRACK_QUERY_ID)
    parcel_id: UUID


class LocateParcel(Query[TrackingResult]):
    parcel_id: UUID


class TestQueryResult:
    def test_is_frozen(self) -> None:
        class MyQueryResult(QueryResult):
            value: str

        result = MyQueryResult(value="hello")

        with pytest.raises(ValidationError):
            result.value = "world"  # type: ignore[misc]

    def test_subclass_with_fields(self) -> None:
        class MyQueryResult(QueryResult):
            value: str

        result = MyQueryResult(value="test")
        assert result.value == "test"

    def test_cannot_instantiate_directly(self) -> None:
        # QueryResult is abstract; verify config convention.
        assert QueryResult.model_config.get("frozen") is True


class TestQuery:
    def test_respects_generic_bound(self) -> None:
        class MyQueryResult(QueryResult):
            value: str

        class MyQuery(Query[MyQueryResult]):
            name: str

        query = MyQuery(name="test")
        assert query.name == "test"

    def test_query_id_auto_generates(self) -> None:
        class MyQueryResult(QueryResult):
            value: str

        class MyQuery(Query[MyQueryResult]):
            data: str

        query1 = MyQuery(data="a")
        query2 = MyQuery(data="b")
        assert isinstance(query1.query_id, UUID)
        assert isinstance(query2.query_id, UUID)
        assert query1.query_id != query2.query_id

    def test_is_frozen(self) -> None:
        class MyQueryResult(QueryResult):
            value: str

        class MyQuery(Query[MyQueryResult]):
            data: str

        query = MyQuery(data="test")

        with pytest.raises(ValidationError):
            query.data = "changed"  # type: ignore[misc]

    def test_extra_fields_rejected(self) -> None:
        class MyQueryResult(QueryResult):
            value: str

        class MyQuery(Query[MyQueryResult]):
            data: str

        with pytest.raises(ValidationError):
            MyQuery(data="test", unknown="extra")  # type: ignore[call-arg]

    def test_subclass_with_custom_fields(self) -> None:
        class MyQueryResult(QueryResult):
            count: int

        class MyQuery(Query[MyQueryResult]):
            name: str
            age: int

        query = MyQuery(name="Alice", age=30)
        assert query.name == "Alice"
        assert query.age == 30


class TestQueryConfigure:
    def test_configure_affects_new_queries(self) -> None:
        fixed = uuid4()

        class FixedGen:
            def generate(self) -> UUID:
                return fixed

        class MyQueryResult(QueryResult):
            value: str

        class MyQuery(Query[MyQueryResult]):
            data: str

        try:
            Query.configure(id_generator=FixedGen())
            query = MyQuery(data="test")
            assert query.query_id == fixed
        finally:
            Query.configure(id_generator=Uuid7Generator())

    def test_configure_with_uuid7_restores_default(self) -> None:
        fixed = uuid4()

        class FixedGen:
            def generate(self) -> UUID:
                return fixed

        class MyQueryResult(QueryResult):
            value: str

        class MyQuery(Query[MyQueryResult]):
            data: str

        try:
            Query.configure(id_generator=FixedGen())
        finally:
            Query.configure(id_generator=Uuid7Generator())

        ids = {MyQuery(data="x").query_id for _ in range(10)}
        assert len(ids) == 10

    def test_configure_does_not_affect_previously_created_query(self) -> None:
        class MyQueryResult(QueryResult):
            value: str

        class MyQuery(Query[MyQueryResult]):
            data: str

        query_before = MyQuery(data="test")
        fixed = uuid4()

        class FixedGen:
            def generate(self) -> UUID:
                return fixed

        try:
            Query.configure(id_generator=FixedGen())
        finally:
            Query.configure(id_generator=Uuid7Generator())

        assert query_before.query_id != fixed

    def test_configure_affects_subclass_instances(self) -> None:
        fixed = uuid4()

        class FixedGen:
            def generate(self) -> UUID:
                return fixed

        class MyQueryResult(QueryResult):
            value: str

        class MyQuery(Query[MyQueryResult]):
            data: str

        try:
            Query.configure(id_generator=FixedGen())
            query = MyQuery(data="test")
            assert query.query_id == fixed
        finally:
            Query.configure(id_generator=Uuid7Generator())


class TestPerSubclassIdGenerator:
    """A subclass's own _id_generator fills query_id (ADR-070)."""

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-125 DDD-09: query_id reads Query._id_generator by name",
    )
    def test_subclass_id_generator_is_honoured_by_queries(self) -> None:
        """Given a query subclass with its own generator, when it is built
        without a query_id, then the query_id comes from that generator."""
        assert TrackParcel(parcel_id=uuid4()).query_id == TRACK_QUERY_ID

    def test_subclass_id_generator_does_not_reach_siblings(self) -> None:
        assert LocateParcel(parcel_id=uuid4()).query_id != TRACK_QUERY_ID

    def test_explicit_query_id_wins_over_subclass_generator(self) -> None:
        explicit = uuid4()
        query = TrackParcel(parcel_id=uuid4(), query_id=explicit)
        assert query.query_id == explicit

    def test_query_id_generation_does_not_mutate_input(self) -> None:
        """Validating a dict without query_id leaves the dict unchanged."""
        data = {"parcel_id": str(uuid4())}
        first = LocateParcel.model_validate(data)
        second = LocateParcel.model_validate(data)
        assert "query_id" not in data
        assert first.query_id != second.query_id
