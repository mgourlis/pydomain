from __future__ import annotations

from pydomain.cqrs import Command, CommandHandler, CommandResult, EmptyCommandResult


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
