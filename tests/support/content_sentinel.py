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
from collections.abc import Iterable
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
    raise NotImplementedError


def assert_absent_from_reprs(
    *objects: object, sentinel: str = CONTENT_SENTINEL
) -> None:
    """Assert that neither ``repr()`` nor ``str()`` of any object shows *sentinel*.

    For an exception, its formatted traceback, including chained causes, is
    checked too.
    """
    raise NotImplementedError


def assert_absent_from_strings(
    strings: Iterable[str], sentinel: str = CONTENT_SENTINEL
) -> None:
    """Assert that none of *strings* (e.g. stored errors) contains *sentinel*."""
    raise NotImplementedError
