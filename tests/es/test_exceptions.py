"""Tests for the ES-specific exception classes.

Covers the StreamNotFoundError exception,
including its inheritance from DomainError, aggregate_id attribute
storage, and descriptive error messages.
"""

from __future__ import annotations

import copy
import pickle
from collections.abc import Callable
from typing import Any

import pytest

from pydomain.ddd.exceptions import DomainError
from pydomain.es.exceptions import (
    DuplicateCommandError,
    SnapshotRestoreError,
    StaleSnapshotError,
    StreamNotFoundError,
)

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


# ===================================================================
# DCE-152 (B-210): pickle and copy keep the message and the attributes
# ===================================================================

_ROUND_TRIPS = pytest.mark.parametrize(
    "round_trip",
    [lambda e: pickle.loads(pickle.dumps(e)), copy.copy, copy.deepcopy],
    ids=["pickle", "copy", "deepcopy"],
)

_SAMPLES: list[Any] = [
    pytest.param(lambda: StreamNotFoundError("order-123"), id="StreamNotFoundError"),
    pytest.param(
        lambda: DuplicateCommandError("order-123", "command-7"),
        id="DuplicateCommandError",
    ),
    pytest.param(
        lambda: SnapshotRestoreError(
            "Order", "order-123", 4, "ValidationError at total (missing)"
        ),
        id="SnapshotRestoreError",
    ),
    pytest.param(
        lambda: StaleSnapshotError("order-123", 1, 2), id="StaleSnapshotError"
    ),
]


class TestRoundTrip:
    """B-210: each ES exception survives pickle, ``copy`` and ``deepcopy``."""

    @_ROUND_TRIPS
    @pytest.mark.parametrize("build", _SAMPLES)
    def test_es_exception_survives_pickle_and_copy(
        self, build: Callable[[], Exception], round_trip: Callable[[Any], Any]
    ) -> None:
        exc = build()

        restored = round_trip(exc)

        assert type(restored) is type(exc)
        assert str(restored) == str(exc)
        assert restored.args == exc.args
        assert vars(restored) == vars(exc)


class _RegionalStreamNotFound(StreamNotFoundError):
    def __init__(self, aggregate_id: str, region: str) -> None:
        self.region = region
        super().__init__(aggregate_id)


class _RetriedDuplicate(DuplicateCommandError):
    def __init__(self, command_id: str) -> None:
        super().__init__("order-123", command_id)


class _SchemaSnapshotRestoreError(SnapshotRestoreError):
    def __init__(self, aggregate_id: str) -> None:
        super().__init__("Order", aggregate_id, 4, "a schema change")


class _OldSnapshot(StaleSnapshotError):
    def __init__(self, aggregate_id: str, *, expected: int) -> None:
        super().__init__(aggregate_id, expected - 1, expected)


class TestSubclassRoundTrip:
    """B-210 (review 9): a subclass with another ``__init__`` signature
    round-trips too, because the restorer never calls ``__init__``."""

    @pytest.mark.xfail(
        strict=True,
        raises=(AssertionError, TypeError),
        reason="DCE-152 B-210: __reduce__ passes the base class's arguments "
        "to the subclass's own __init__",
    )
    @_ROUND_TRIPS
    @pytest.mark.parametrize(
        "build",
        [
            lambda: _RegionalStreamNotFound("order-123", region="eu"),
            lambda: _RetriedDuplicate("command-7"),
            lambda: _SchemaSnapshotRestoreError("order-123"),
            lambda: _OldSnapshot("order-123", expected=2),
        ],
        ids=[
            "StreamNotFoundError",
            "DuplicateCommandError",
            "SnapshotRestoreError",
            "StaleSnapshotError",
        ],
    )
    def test_es_subclass_with_another_signature_survives_pickle_and_copy(
        self, build: Callable[[], Exception], round_trip: Callable[[Any], Any]
    ) -> None:
        exc = build()

        restored = round_trip(exc)

        assert type(restored) is type(exc)
        assert str(restored) == str(exc)
        assert restored.args == exc.args
        assert vars(restored) == vars(exc)
