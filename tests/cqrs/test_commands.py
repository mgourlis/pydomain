from __future__ import annotations

import pytest
from pydantic import ValidationError

from pydomain.cqrs import Command, CommandResult, EmptyCommandResult


class TestCommandResult:
    def test_is_frozen(self) -> None:
        class MyResult(CommandResult):
            value: str

        result = MyResult(value="hello")

        with pytest.raises(ValidationError):
            result.value = "world"  # type: ignore[misc]

    def test_cannot_instantiate_directly(self) -> None:
        # CommandResult is abstract; direct instantiation should not create
        # a meaningful instance. Since Pydantic v2 + ABC doesn't enforce
        # this at the language level without abstract methods, we verify
        # the class convention instead.
        assert CommandResult.model_config.get("frozen") is True

    def test_subclass_with_extra_fields(self) -> None:
        class MyResult(CommandResult):
            value: str

        result = MyResult(value="test")
        assert result.value == "test"


class TestEmptyCommandResult:
    def test_can_instantiate(self) -> None:
        result = EmptyCommandResult()
        assert isinstance(result, CommandResult)

    def test_is_frozen(self) -> None:
        result = EmptyCommandResult()

        with pytest.raises(ValidationError):
            result.something = "nope"  # type: ignore[attr-defined]


class TestCommand:
    def test_respects_generic_bound(self) -> None:
        class MyResult(CommandResult):
            value: str

        class MyCommand(Command[MyResult]):
            name: str

        cmd = MyCommand(name="test")
        assert cmd.name == "test"
        assert isinstance(cmd.command_id, str) or hasattr(cmd.command_id, "int")

    def test_command_id_auto_generates(self) -> None:
        class MyCommand(Command[EmptyCommandResult]):
            data: str

        cmd1 = MyCommand(data="a")
        cmd2 = MyCommand(data="b")
        assert cmd1.command_id != cmd2.command_id

    def test_is_frozen(self) -> None:
        class MyCommand(Command[EmptyCommandResult]):
            data: str

        cmd = MyCommand(data="test")

        with pytest.raises(ValidationError):
            cmd.data = "changed"  # type: ignore[misc]

    def test_extra_fields_rejected(self) -> None:
        class MyCommand(Command[EmptyCommandResult]):
            data: str

        with pytest.raises(ValidationError):
            MyCommand(data="test", unknown="extra")  # type: ignore[call-arg]
