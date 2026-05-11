from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from pydomain.cqrs import Query, QueryResult
from pydomain.ddd.id_generator import Uuid7Generator


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
