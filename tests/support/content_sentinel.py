"""Content sentinel: prove that content never reaches logs, reprs or errors.

Put :data:`CONTENT_SENTINEL` into a content field (a payload, a narrative, a
note), run the code, then assert that the sentinel is absent from what was
logged, from the ``repr()`` of what was built and from stored error strings::

    event = InvoiceIssued(invoice_id=uuid4(), note=CONTENT_SENTINEL)
    ...
    assert_absent_from_logs(caplog)
    assert_absent_from_reprs(event, entry)
    assert_absent_from_strings(entry.errors)
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Iterable, Iterator
from typing import Final

import pytest

CONTENT_SENTINEL: Final = "SENTINEL-synthetic-content-7f3c9a"
"""A fixed synthetic string that stands in for user content."""


def assert_absent_from_logs(
    records: pytest.LogCaptureFixture | Iterable[logging.LogRecord],
    sentinel: str = CONTENT_SENTINEL,
) -> None:
    """Assert that no captured log record carries *sentinel*.

    Checks the formatted message, the raw message and arguments, any
    ``extra`` fields, and the formatted exception and stack.
    """
    if isinstance(records, pytest.LogCaptureFixture):
        records = records.records
    for index, record in enumerate(records):
        for where, text in _record_texts(record):
            if sentinel in text:
                raise AssertionError(
                    f"content sentinel in log record [{index}] ({where}; logger "
                    f"{record.name!r}, level {record.levelname})"
                )


def assert_absent_from_reprs(
    *objects: object, sentinel: str = CONTENT_SENTINEL
) -> None:
    """Assert that neither ``repr()`` nor ``str()`` of any object shows *sentinel*.

    For an exception, its formatted traceback, including chained causes, is
    checked too.
    """
    for index, obj in enumerate(objects):
        for where, text in _object_texts(obj):
            if sentinel in text:
                raise AssertionError(
                    f"content sentinel in {where} of object [{index}] "
                    f"({type(obj).__qualname__})"
                )


def assert_absent_from_strings(
    strings: Iterable[str], sentinel: str = CONTENT_SENTINEL
) -> None:
    """Assert that none of *strings* (e.g. stored errors) contains *sentinel*."""
    for index, text in enumerate(strings):
        if sentinel in text:
            raise AssertionError(f"content sentinel in string [{index}]")


def _record_texts(record: logging.LogRecord) -> Iterator[tuple[str, str]]:
    try:
        yield "message", record.getMessage()
    except Exception:  # a broken format string still has its parts checked below
        pass
    # msg, args, extra fields, exc_text and stack_info
    yield "fields", repr(vars(record))
    if isinstance(record.exc_info, tuple):
        yield "exception", logging.Formatter().formatException(record.exc_info)


def _object_texts(obj: object) -> Iterator[tuple[str, str]]:
    yield "repr()", repr(obj)
    yield "str()", str(obj)
    if isinstance(obj, BaseException):
        yield "traceback", "".join(traceback.format_exception(obj))
