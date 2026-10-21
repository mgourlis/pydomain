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


class _QuietError(Exception):
    """Synthetic exception whose str() hides its arguments; repr() shows them."""

    def __str__(self) -> str:
        return "quiet"


class _UnreadableStr:
    """Synthetic object whose str() raises."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __repr__(self) -> str:
        return f"_UnreadableStr({self.text!r})"

    def __str__(self) -> str:
        raise RuntimeError("str() unavailable")


class _UnreadableRepr:
    """Synthetic object whose repr() raises."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __repr__(self) -> str:
        raise RuntimeError("repr() unavailable")

    def __str__(self) -> str:
        return self.text


_MISSES_CHAINS = pytest.mark.xfail(
    strict=True,
    raises=pytest.fail.Exception,
    reason="DCE-108 review: chained exceptions are checked only as traceback text",
)
_CRASHES_ON_UNREADABLE = pytest.mark.xfail(
    strict=True,
    raises=RuntimeError,
    reason="DCE-108 review: a raising __str__ or __repr__ crashes the check",
)


@_MISSES_CHAINS
def test_sentinel_in_suppressed_context_fails() -> None:
    try:
        try:
            raise ValueError(CONTENT_SENTINEL)
        except ValueError:
            raise RuntimeError("handler failed") from None
    except RuntimeError as error:
        outer = error

    with pytest.raises(AssertionError):
        assert_absent_from_reprs(outer)


@_MISSES_CHAINS
def test_sentinel_in_the_repr_of_a_cause_fails() -> None:
    try:
        try:
            raise _QuietError(CONTENT_SENTINEL)
        except _QuietError as cause:
            raise RuntimeError("handler failed") from cause
    except RuntimeError as error:
        outer = error

    with pytest.raises(AssertionError):
        assert_absent_from_reprs(outer)


@_MISSES_CHAINS
def test_sentinel_in_the_cause_of_a_group_member_fails() -> None:
    member = RuntimeError("handler failed")
    member.__cause__ = _QuietError(CONTENT_SENTINEL)
    group = ExceptionGroup("handlers failed", [member])

    with pytest.raises(AssertionError):
        assert_absent_from_reprs(group)


def test_chained_exceptions_without_the_sentinel_pass() -> None:
    try:
        try:
            raise ValueError("invalid quantity")
        except ValueError:
            raise RuntimeError("handler failed") from None
    except RuntimeError as error:
        outer = error

    assert_absent_from_reprs(outer, ExceptionGroup("failed", [ValueError("x")]))


def test_sentinel_in_repr_fails_when_str_raises() -> None:
    with pytest.raises(AssertionError, match=r"repr\(\)"):
        assert_absent_from_reprs(_UnreadableStr(CONTENT_SENTINEL))


@_CRASHES_ON_UNREADABLE
def test_sentinel_in_str_fails_when_repr_raises() -> None:
    with pytest.raises(AssertionError, match=r"str\(\)"):
        assert_absent_from_reprs(_UnreadableRepr(CONTENT_SENTINEL))


@_CRASHES_ON_UNREADABLE
def test_unreadable_objects_without_the_sentinel_pass() -> None:
    assert_absent_from_reprs(_UnreadableStr("INV-1"), _UnreadableRepr("INV-1"))
