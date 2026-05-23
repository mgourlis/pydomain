"""Tests for saga exception hierarchy and raise conditions."""

from __future__ import annotations

import pytest

from pydomain.cqrs.saga.exceptions import (
    SagaConfigurationError,
    SagaError,
    SagaHandlerNotFoundError,
    SagaStateError,
)


class TestExceptionHierarchy:
    """All saga exceptions inherit from SagaError."""

    def test_saga_error_is_base(self) -> None:
        assert issubclass(SagaConfigurationError, SagaError)
        assert issubclass(SagaHandlerNotFoundError, SagaError)
        assert issubclass(SagaStateError, SagaError)

    def test_saga_error_is_exception(self) -> None:
        assert issubclass(SagaError, Exception)

    def test_exceptions_are_distinct(self) -> None:
        assert SagaConfigurationError is not SagaHandlerNotFoundError
        assert SagaConfigurationError is not SagaStateError
        assert SagaHandlerNotFoundError is not SagaStateError


class TestExceptionMessages:
    """Exceptions carry meaningful messages."""

    def test_saga_error_message(self) -> None:
        err = SagaError("base error")
        assert str(err) == "base error"

    def test_configuration_error_message(self) -> None:
        err = SagaConfigurationError("bad config")
        assert "bad config" in str(err)

    def test_handler_not_found_message(self) -> None:
        err = SagaHandlerNotFoundError("No handler for FooEvent")
        assert "No handler for FooEvent" in str(err)

    def test_state_error_message(self) -> None:
        err = SagaStateError("invalid transition")
        assert "invalid transition" in str(err)


class TestExceptionCatching:
    """Catching SagaError catches all subtypes."""

    def test_catch_configuration_error_as_saga_error(self) -> None:
        with pytest.raises(SagaError):
            raise SagaConfigurationError("test")

    def test_catch_handler_not_found_as_saga_error(self) -> None:
        with pytest.raises(SagaError):
            raise SagaHandlerNotFoundError("test")

    def test_catch_state_error_as_saga_error(self) -> None:
        with pytest.raises(SagaError):
            raise SagaStateError("test")

    def test_catch_all_as_exception(self) -> None:
        for exc_cls in (
            SagaError,
            SagaConfigurationError,
            SagaHandlerNotFoundError,
            SagaStateError,
        ):
            with pytest.raises(Exception):
                raise exc_cls("test")


class TestExceptionSubclassing:
    """Custom exceptions can be created from SagaError."""

    def test_custom_saga_error(self) -> None:
        class PaymentSagaError(SagaError):
            pass

        err = PaymentSagaError("payment failed")
        assert isinstance(err, SagaError)
        assert isinstance(err, Exception)
        assert str(err) == "payment failed"
