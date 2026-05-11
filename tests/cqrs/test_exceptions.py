from __future__ import annotations

from uuid import uuid4

import pytest

from pydomain.cqrs import (
    CommandExecutionError,
    CQRSError,
    HandlerAlreadyRegisteredError,
    IdempotentCommandIgnored,
    NoHandlerRegisteredError,
)
from pydomain.ddd.exceptions import DomainError
from tests.cqrs.conftest import MakeGreeting


class TestCQRSError:
    def test_inherits_from_domain_error(self) -> None:
        assert issubclass(CQRSError, DomainError)

    def test_is_raiseable(self) -> None:
        with pytest.raises(CQRSError):
            raise CQRSError


class TestHandlerAlreadyRegisteredError:
    def test_inherits_from_cqrs_error(self) -> None:
        assert issubclass(HandlerAlreadyRegisteredError, CQRSError)

    def test_with_message(self) -> None:
        exc = HandlerAlreadyRegisteredError("test error")
        assert str(exc) == "test error"


class TestNoHandlerRegisteredError:
    def test_inherits_from_cqrs_error(self) -> None:
        assert issubclass(NoHandlerRegisteredError, CQRSError)

    def test_with_message(self) -> None:
        exc = NoHandlerRegisteredError("test error")
        assert str(exc) == "test error"


class TestCommandExecutionError:
    def test_inherits_from_cqrs_error(self) -> None:
        assert issubclass(CommandExecutionError, CQRSError)

    def test_inherits_from_domain_error(self) -> None:
        assert issubclass(CommandExecutionError, DomainError)

    def test_carries_command(self) -> None:
        cmd = MakeGreeting(command_id=uuid4(), name="test")
        exc = CommandExecutionError(cmd)
        assert exc.command is cmd

    def test_default_message_includes_command_name(self) -> None:
        cmd = MakeGreeting(command_id=uuid4(), name="test")
        exc = CommandExecutionError(cmd)
        assert "MakeGreeting" in str(exc)

    def test_custom_message_overrides_default(self) -> None:
        cmd = MakeGreeting(command_id=uuid4(), name="test")
        exc = CommandExecutionError(cmd, message="custom")
        assert str(exc) == "custom"

    def test_preserves_cause(self) -> None:
        cmd = MakeGreeting(command_id=uuid4(), name="test")
        try:
            raise CommandExecutionError(cmd) from ValueError("original")
        except CommandExecutionError as e:
            assert isinstance(e.__cause__, ValueError)
            assert str(e.__cause__) == "original"

    def test_caught_by_cqrs_error(self) -> None:
        cmd = MakeGreeting(command_id=uuid4(), name="test")
        with pytest.raises(CQRSError):
            raise CommandExecutionError(cmd)


class TestIdempotentCommandIgnored:
    def test_inherits_from_cqrs_error(self) -> None:
        assert issubclass(IdempotentCommandIgnored, CQRSError)

    def test_inherits_from_domain_error(self) -> None:
        assert issubclass(IdempotentCommandIgnored, DomainError)

    def test_carries_command_id(self) -> None:
        cid = uuid4()
        exc = IdempotentCommandIgnored(cid)
        assert exc.command_id == cid

    def test_default_message_includes_command_id(self) -> None:
        cid = uuid4()
        exc = IdempotentCommandIgnored(cid)
        assert str(cid) in str(exc)

    def test_custom_message_overrides_default(self) -> None:
        exc = IdempotentCommandIgnored(uuid4(), message="custom")
        assert str(exc) == "custom"

    def test_caught_by_cqrs_error(self) -> None:
        with pytest.raises(CQRSError):
            raise IdempotentCommandIgnored(uuid4())
