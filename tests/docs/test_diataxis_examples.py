"""Run the Python examples of every Diataxis page marked runnable.

A page opts in with ``<!-- docs-test: runnable -->``; the convention is in
docs/diataxis/how-to/testing/add-runnable-docs-examples.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.docs.doc_examples import run_page, runnable_pages

DIATAXIS = Path(__file__).resolve().parents[2] / "docs" / "diataxis"
PAGES = runnable_pages(DIATAXIS)


@pytest.mark.parametrize(
    "page", PAGES, ids=[str(page.relative_to(DIATAXIS)) for page in PAGES]
)
def test_page_examples_run(page: Path) -> None:
    run_page(page)


def test_runnable_pages_are_found() -> None:
    assert PAGES, f"no page under {DIATAXIS} is marked runnable"
