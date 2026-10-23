from __future__ import annotations

import copy
import pickle
from collections.abc import Callable
from typing import Any
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
    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-126 TEST-03: CQRS-K4, a DomainError until W3-1",
    )
    def test_is_not_a_domain_error(self) -> None:
        """CQRSError is its own root, apart from DomainError (ADR-004, W3-1).

        Until DCE-126 this test asserted the opposite, blessing CQRS-K4.
        """
        assert not issubclass(CQRSError, DomainError)

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

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-126 TEST-03: CQRS-K4, a DomainError until W3-1",
    )
    def test_is_not_a_domain_error(self) -> None:
        """Until DCE-126 this test asserted the opposite, blessing CQRS-K4."""
        assert not issubclass(CommandExecutionError, DomainError)

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


_ROUND_TRIPS = pytest.mark.parametrize(
    "round_trip",
    [lambda e: pickle.loads(pickle.dumps(e)), copy.copy, copy.deepcopy],
    ids=["pickle", "copy", "deepcopy"],
)


class TestCommandExecutionErrorRoundTrip:
    """DCE-152 (B-208): pickle and copy keep the message and the command."""

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-152 B-208: args=(message,) comes back as the command",
    )
    @_ROUND_TRIPS
    @pytest.mark.parametrize("message", [None, "custom"], ids=["default", "custom"])
    def test_command_execution_error_survives_pickle_and_copy(
        self, round_trip: Callable[[Any], Any], message: str | None
    ) -> None:
        cmd = MakeGreeting(command_id=uuid4(), name="test")
        exc = CommandExecutionError(cmd, message=message)

        restored = round_trip(exc)

        assert type(restored) is CommandExecutionError
        assert str(restored) == str(exc)
        assert restored.args == exc.args
        assert restored.command == cmd


class TestIdempotentCommandIgnoredRoundTrip:
    """DCE-152 (B-208): the same defect, ``command_id`` in place of the command."""

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-152 B-208: args=(message,) comes back as the command_id",
    )
    @_ROUND_TRIPS
    @pytest.mark.parametrize("message", [None, "custom"], ids=["default", "custom"])
    def test_idempotent_command_ignored_survives_pickle_and_copy(
        self, round_trip: Callable[[Any], Any], message: str | None
    ) -> None:
        command_id = uuid4()
        exc = IdempotentCommandIgnored(command_id, message=message)

        restored = round_trip(exc)

        assert type(restored) is IdempotentCommandIgnored
        assert str(restored) == str(exc)
        assert restored.args == exc.args
        assert restored.command_id == command_id


class TestIdempotentCommandIgnored:
    def test_inherits_from_cqrs_error(self) -> None:
        assert issubclass(IdempotentCommandIgnored, CQRSError)

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-126 TEST-03: CQRS-K4, a DomainError until W3-1",
    )
    def test_is_not_a_domain_error(self) -> None:
        """Until DCE-126 this test asserted the opposite, blessing CQRS-K4."""
        assert not issubclass(IdempotentCommandIgnored, DomainError)

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
