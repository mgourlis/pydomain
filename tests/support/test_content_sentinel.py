"""Tests for the content sentinel helpers."""

from __future__ import annotations

import logging

import pytest

from tests.support.content_sentinel import (
    CONTENT_SENTINEL,
    assert_absent_from_logs,
    assert_absent_from_reprs,
    assert_absent_from_strings,
)

pytestmark = pytest.mark.xfail(
    strict=True, raises=NotImplementedError, reason="DCE-108 content sentinel"
)

logger = logging.getLogger("tests.support.sentinel")


class _Note:
    """Synthetic object whose str() shows content but repr() does not."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __repr__(self) -> str:
        return "_Note(...)"

    def __str__(self) -> str:
        return self.text


def test_logs_without_the_sentinel_pass(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        logger.info("invoice %s issued", "INV-1")

    assert_absent_from_logs(caplog)


def test_sentinel_in_log_arguments_fails(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        logger.info("note: %s", CONTENT_SENTINEL)

    with pytest.raises(AssertionError):
        assert_absent_from_logs(caplog)


def test_sentinel_in_extra_fields_fails(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        logger.info("invoice issued", extra={"payload": {"note": CONTENT_SENTINEL}})

    with pytest.raises(AssertionError):
        assert_absent_from_logs(caplog)


def test_sentinel_in_logged_exception_fails(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        try:
            raise ValueError(CONTENT_SENTINEL)
        except ValueError:
            logger.exception("handler failed")

    with pytest.raises(AssertionError):
        assert_absent_from_logs(caplog)


def test_accepts_a_list_of_records() -> None:
    record = logging.LogRecord(
        "tests", logging.INFO, __file__, 1, "note: %s", (CONTENT_SENTINEL,), None
    )

    with pytest.raises(AssertionError):
        assert_absent_from_logs([record])


def test_reprs_without_the_sentinel_pass() -> None:
    assert_absent_from_reprs({"invoice": "INV-1"}, ValueError("boom"), 42)


def test_sentinel_in_repr_fails() -> None:
    with pytest.raises(AssertionError):
        assert_absent_from_reprs({"note": CONTENT_SENTINEL})


def test_sentinel_in_str_fails() -> None:
    with pytest.raises(AssertionError):
        assert_absent_from_reprs(_Note(CONTENT_SENTINEL))


def test_sentinel_in_chained_exception_cause_fails() -> None:
    try:
        try:
            raise ValueError(CONTENT_SENTINEL)
        except ValueError as cause:
            raise RuntimeError("handler failed") from cause
    except RuntimeError as error:
        outer = error

    with pytest.raises(AssertionError):
        assert_absent_from_reprs(outer)


def test_strings_without_the_sentinel_pass() -> None:
    assert_absent_from_strings(["ValueError: invalid quantity", "timeout"])


def test_sentinel_in_a_stored_error_string_fails() -> None:
    errors = ["timeout", f"ValueError: {CONTENT_SENTINEL}"]

    with pytest.raises(AssertionError, match=r"\[1\]"):
        assert_absent_from_strings(errors)


def test_a_custom_sentinel_can_be_given() -> None:
    with pytest.raises(AssertionError):
        assert_absent_from_strings(["shipment SECRET-1"], sentinel="SECRET-1")
