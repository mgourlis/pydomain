from __future__ import annotations

import pytest

from pydomain.cqrs import (
    CQRSError,
    HandlerAlreadyRegisteredError,
    NoHandlerRegisteredError,
)
from pydomain.ddd.exceptions import DomainError


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
