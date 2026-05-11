from __future__ import annotations

from pydomain.cqrs import Query, QueryResult
from pydomain.cqrs.handlers import QueryHandler


class TestQueryHandler:
    def test_is_runtime_checkable(self) -> None:
        class MyQueryResult(QueryResult):
            value: str

        class MyQuery(Query[MyQueryResult]):
            data: str

        class ValidHandler:
            async def __call__(self, query: MyQuery) -> MyQueryResult:
                return MyQueryResult(value=query.data)

        assert isinstance(ValidHandler(), QueryHandler)

    def test_rejects_non_handler(self) -> None:
        class NotHandler:
            pass

        assert not isinstance(NotHandler(), QueryHandler)

    def test_sync_callable_is_instance(self) -> None:
        """A sync callable with the right signature matches the protocol.

        ``@runtime_checkable`` does not distinguish sync from async at
        the ``isinstance`` level; it only checks structural conformance.
        """

        class MyQueryResult(QueryResult):
            value: str

        class MyQuery(Query[MyQueryResult]):
            data: str

        class SyncHandler:
            def __call__(self, query: MyQuery) -> MyQueryResult:
                return MyQueryResult(value=query.data)

        assert isinstance(SyncHandler(), QueryHandler)

    def test_handler_without_uow(self) -> None:
        """QueryHandler has NO UoW parameter — only the query."""

        class MyQueryResult(QueryResult):
            value: str

        class MyQuery(Query[MyQueryResult]):
            data: str

        class SimpleHandler:
            async def __call__(self, query: MyQuery) -> MyQueryResult:
                return MyQueryResult(value=query.data)

        assert isinstance(SimpleHandler(), QueryHandler)
