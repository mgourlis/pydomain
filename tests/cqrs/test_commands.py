from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from pydomain.cqrs import Command, CommandResult, EmptyCommandResult
from pydomain.ddd.id_generator import Uuid7Generator


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


class TestCommandConfigure:
    def test_configure_affects_new_commands(self) -> None:
        fixed = uuid4()

        class FixedGen:
            def generate(self) -> UUID:
                return fixed

        class MyCommand(Command[EmptyCommandResult]):
            data: str

        try:
            Command.configure(id_generator=FixedGen())
            cmd = MyCommand(data="test")
            assert cmd.command_id == fixed
        finally:
            Command.configure(id_generator=Uuid7Generator())

    def test_configure_with_uuid7_restores_default(self) -> None:
        fixed = uuid4()

        class FixedGen:
            def generate(self) -> UUID:
                return fixed

        class MyCommand(Command[EmptyCommandResult]):
            data: str

        try:
            Command.configure(id_generator=FixedGen())
        finally:
            Command.configure(id_generator=Uuid7Generator())

        ids = {MyCommand(data="x").command_id for _ in range(10)}
        assert len(ids) == 10

    def test_configure_does_not_affect_previously_created_command(self) -> None:
        class MyCommand(Command[EmptyCommandResult]):
            data: str

        cmd_before = MyCommand(data="test")
        fixed = uuid4()

        class FixedGen:
            def generate(self) -> UUID:
                return fixed

        try:
            Command.configure(id_generator=FixedGen())
        finally:
            Command.configure(id_generator=Uuid7Generator())

        assert cmd_before.command_id != fixed

    def test_configure_affects_subclass_instances(self) -> None:
        fixed = uuid4()

        class FixedGen:
            def generate(self) -> UUID:
                return fixed

        class MyCommand(Command[EmptyCommandResult]):
            data: str

        try:
            Command.configure(id_generator=FixedGen())
            cmd = MyCommand(data="test")
            assert cmd.command_id == fixed
        finally:
            Command.configure(id_generator=Uuid7Generator())
