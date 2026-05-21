"""Tests for the ES-specific exception classes.

Covers the StreamNotFoundError exception,
including its inheritance from DomainError, aggregate_id attribute
storage, and descriptive error messages.
"""

from __future__ import annotations

import pytest

from pydomain.ddd.exceptions import DomainError
from pydomain.es.exceptions import StreamNotFoundError

# ===================================================================
# StreamNotFoundError
# ===================================================================


class TestStreamNotFoundError:
    """``StreamNotFoundError`` -- raised when an event stream does not
    exist."""

    def test_extends_domain_error(self) -> None:
        """``StreamNotFoundError`` is an instance of ``DomainError``."""
        exc = StreamNotFoundError(aggregate_id="order-123")
        assert isinstance(exc, DomainError)

    def test_extends_exception(self) -> None:
        """``StreamNotFoundError`` is an instance of ``Exception``."""
        exc = StreamNotFoundError(aggregate_id="order-123")
        assert isinstance(exc, Exception)

    def test_stores_aggregate_id(self) -> None:
        """The ``aggregate_id`` passed at construction is stored as an
        instance attribute."""
        exc = StreamNotFoundError(aggregate_id="order-123")
        assert exc.aggregate_id == "order-123"

    def test_descriptive_message(self) -> None:
        """The exception message includes the aggregate_id in a human-
        readable format."""
        exc = StreamNotFoundError(aggregate_id="order-123")

        assert str(exc) == "Event stream for aggregate 'order-123' not found."

    def test_empty_aggregate_id(self) -> None:
        """An empty string ``aggregate_id`` does not cause errors."""
        exc = StreamNotFoundError(aggregate_id="")

        assert exc.aggregate_id == ""
        assert str(exc) == "Event stream for aggregate '' not found."


# ===================================================================
# Caught as DomainError
# ===================================================================


class TestCaughtAsDomainError:
    """ES exceptions can be caught via the ``DomainError`` base class."""

    def test_catch_stream_not_found_as_domain_error(self) -> None:
        """``StreamNotFoundError`` is caught by ``except DomainError``."""
        with pytest.raises(DomainError) as exc_info:
            raise StreamNotFoundError(aggregate_id="cart-001")

        assert isinstance(exc_info.value, StreamNotFoundError)
        assert exc_info.value.aggregate_id == "cart-001"
