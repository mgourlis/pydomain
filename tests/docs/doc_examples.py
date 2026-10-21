"""Run the fenced Python examples of Diataxis pages marked runnable.

Not collected by pytest itself (no ``test_`` prefix); used by
``test_diataxis_examples.py`` and tested by ``test_doc_examples.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

RUNNABLE_MARKER = "<!-- docs-test: runnable -->"
SKIP_MARKER = "<!-- docs-test: skip -->"
SETUP_OPENER = "<!-- docs-test: setup"


class DocsExampleError(Exception):
    """A block of a runnable page failed; the message names page and line."""


@dataclass(frozen=True)
class Block:
    """One Python block of a page: the line of its opening fence and its code."""

    line: int
    code: str
    kind: Literal["run", "skip", "setup"]


def parse_page(path: Path) -> list[Block] | None:
    """Return the page's Python blocks, or ``None`` if it is not marked runnable."""
    raise NotImplementedError


def runnable_pages(root: Path) -> list[Path]:
    """Return every Markdown page under ``root`` marked runnable, sorted."""
    raise NotImplementedError


def run_page(path: Path) -> dict[str, object]:
    """Run the page's setup blocks, then its other blocks, in one namespace.

    Returns the namespace. Raises :class:`DocsExampleError` naming the page and
    the failing block's starting line.
    """
    raise NotImplementedError
