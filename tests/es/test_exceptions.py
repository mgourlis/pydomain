"""Tests for the ES-specific exception classes.

Covers the StreamNotFoundError and StreamAlreadyExistsError exceptions,
including their inheritance from DomainError, aggregate_id attribute
storage, and descriptive error messages.
"""

from __future__ import annotations

import pytest

from pydomain.ddd.exceptions import DomainError
from pydomain.es.exceptions import StreamAlreadyExistsError, StreamNotFoundError

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
# StreamAlreadyExistsError
# ===================================================================


class TestStreamAlreadyExistsError:
    """``StreamAlreadyExistsError`` -- raised when trying to create a
    stream that already exists."""

    def test_extends_domain_error(self) -> None:
        """``StreamAlreadyExistsError`` is an instance of
        ``DomainError``."""
        exc = StreamAlreadyExistsError(aggregate_id="order-123")
        assert isinstance(exc, DomainError)

    def test_extends_exception(self) -> None:
        """``StreamAlreadyExistsError`` is an instance of
        ``Exception``."""
        exc = StreamAlreadyExistsError(aggregate_id="order-123")
        assert isinstance(exc, Exception)

    def test_stores_aggregate_id(self) -> None:
        """The ``aggregate_id`` passed at construction is stored as an
        instance attribute."""
        exc = StreamAlreadyExistsError(aggregate_id="order-123")
        assert exc.aggregate_id == "order-123"

    def test_descriptive_message(self) -> None:
        """The exception message includes the aggregate_id in a human-
        readable format."""
        exc = StreamAlreadyExistsError(aggregate_id="order-123")

        assert str(exc) == "Event stream for aggregate 'order-123' already exists."

    def test_empty_aggregate_id(self) -> None:
        """An empty string ``aggregate_id`` does not cause errors."""
        exc = StreamAlreadyExistsError(aggregate_id="")

        assert exc.aggregate_id == ""
        assert str(exc) == "Event stream for aggregate '' already exists."


# ===================================================================
# Both exceptions caught as DomainError
# ===================================================================


class TestCaughtAsDomainError:
    """Both ES exceptions can be caught via the ``DomainError`` base
    class."""

    def test_catch_stream_not_found_as_domain_error(self) -> None:
        """``StreamNotFoundError`` is caught by ``except DomainError``."""
        with pytest.raises(DomainError) as exc_info:
            raise StreamNotFoundError(aggregate_id="cart-001")

        assert isinstance(exc_info.value, StreamNotFoundError)
        assert exc_info.value.aggregate_id == "cart-001"

    def test_catch_stream_already_exists_as_domain_error(self) -> None:
        """``StreamAlreadyExistsError`` is caught by
        ``except DomainError``."""
        with pytest.raises(DomainError) as exc_info:
            raise StreamAlreadyExistsError(aggregate_id="cart-001")

        assert isinstance(exc_info.value, StreamAlreadyExistsError)
        assert exc_info.value.aggregate_id == "cart-001"

    def test_catch_both_in_single_except(self) -> None:
        """Both exception types are caught by a single ``except
        DomainError`` clause."""
        errors = [
            StreamNotFoundError(aggregate_id="a"),
            StreamAlreadyExistsError(aggregate_id="b"),
        ]

        for error in errors:
            with pytest.raises(DomainError):
                raise error
