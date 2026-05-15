from __future__ import annotations

from pydomain.cqrs import (
    Command,
    CommandHandler,
    CommandResult,
    EmptyCommandResult,
    Query,
    QueryResult,
)
from pydomain.cqrs.handlers import EventHandler, QueryHandler
from pydomain.ddd.domain_event import DomainEvent


class TestCommandHandler:
    def test_is_runtime_checkable(self) -> None:
        class MyResult(CommandResult):
            value: str

        class MyCommand(Command[MyResult]):
            data: str

        class ValidHandler:
            async def __call__(self, command: MyCommand) -> MyResult:
                return MyResult(value=command.data)

        assert isinstance(ValidHandler(), CommandHandler)

    def test_rejects_non_handler(self) -> None:
        class NotHandler:
            pass

        assert not isinstance(NotHandler(), CommandHandler)

    def test_sync_callable_is_instance(self) -> None:
        """A sync callable with the right signature matches the protocol.

        ``@runtime_checkable`` does not distinguish sync from async at
        the ``isinstance`` level; it only checks structural conformance.
        """

        class MyCommand(Command[EmptyCommandResult]):
            data: str

        class SyncHandler:
            def __call__(self, command: MyCommand) -> EmptyCommandResult:
                return EmptyCommandResult()

        assert isinstance(SyncHandler(), CommandHandler)

    def test_handler_with_void_result(self) -> None:
        class VoidCommand(Command[EmptyCommandResult]):
            data: str

        class VoidHandler:
            async def __call__(self, command: VoidCommand) -> EmptyCommandResult:
                return EmptyCommandResult()

        assert isinstance(VoidHandler(), CommandHandler)


class TestQueryHandler:
    def test_is_runtime_checkable(self) -> None:
        class MyResult(QueryResult):
            value: str

        class MyQuery(Query[MyResult]):
            data: str

        class ValidHandler:
            async def __call__(self, query: MyQuery) -> MyResult:
                return MyResult(value=query.data)

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

        class MyResult(QueryResult):
            value: str

        class MyQuery(Query[MyResult]):
            data: str

        class SyncHandler:
            def __call__(self, query: MyQuery) -> MyResult:
                return MyResult(value="sync")

        assert isinstance(SyncHandler(), QueryHandler)


class TestEventHandler:
    def test_is_runtime_checkable(self) -> None:
        class MyEvent(DomainEvent):
            data: str

        class ValidHandler:
            async def __call__(self, event: MyEvent) -> None:
                pass

        assert isinstance(ValidHandler(), EventHandler)

    def test_rejects_non_handler(self) -> None:
        class NotHandler:
            pass

        assert not isinstance(NotHandler(), EventHandler)

    def test_sync_callable_is_instance(self) -> None:
        """A sync callable with the right signature matches the protocol.

        ``@runtime_checkable`` does not distinguish sync from async at
        the ``isinstance`` level; it only checks structural conformance.
        """

        class MyEvent(DomainEvent):
            data: str

        class SyncHandler:
            def __call__(self, event: MyEvent) -> None:
                pass

        assert isinstance(SyncHandler(), EventHandler)

    def test_handler_with_constructor_injection(self) -> None:
        """EventHandler can accept constructor arguments (e.g. bus injection).

        The protocol only requires ``__call__`` — constructor parameters
        are not part of the protocol contract.
        """

        class MyEvent(DomainEvent):
            data: str

        class HandlerWithBus:
            def __init__(self, bus: object) -> None:
                self._bus = bus

            async def __call__(self, event: MyEvent) -> None:
                pass

        assert isinstance(HandlerWithBus(bus=object()), EventHandler)
