"""Run the fenced Python examples of Diataxis pages marked runnable.

Not collected by pytest itself (no ``test_`` prefix); used by
``test_diataxis_examples.py`` and tested by ``test_doc_examples.py``.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import re
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from typing import Literal

RUNNABLE_MARKER = "<!-- docs-test: runnable -->"
SKIP_MARKER = "<!-- docs-test: skip -->"
SETUP_OPENER = "<!-- docs-test: setup"
COMMENT_CLOSER = "-->"

_FENCE = re.compile(r"^[ \t]*(?P<fence>`{3,}|~{3,})[ \t]*(?P<info>.*)$")


class DocsExampleError(Exception):
    """A block of a runnable page failed; the message names page and line."""


@dataclass(frozen=True)
class Block:
    """One Python block of a page: the line of its opening fence and its code."""

    line: int
    code: str
    kind: Literal["run", "skip", "setup"]


def parse_page(path: Path) -> list[Block] | None:
    """Return the page's Python blocks, or ``None`` if it is not marked runnable.

    Markers and setup comments are only recognised outside fenced blocks, so a
    page can show them inside a ``markdown`` fence without being affected.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks: list[Block] = []
    runnable = False
    skip_next = False
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        fence = _FENCE.match(lines[index])
        if fence:
            start = index
            body, index = _collect_fence(lines, index + 1, fence["fence"])
            words = fence["info"].split()
            if words and words[0] == "python":
                kind: Literal["run", "skip"] = "skip" if skip_next else "run"
                blocks.append(Block(line=start + 1, code=_code(body, start), kind=kind))
            skip_next = False
        elif stripped == RUNNABLE_MARKER:
            runnable = True
        elif stripped == SKIP_MARKER:
            skip_next = True
        elif stripped == SETUP_OPENER:
            start = index
            body, index = _collect_until(lines, index + 1, COMMENT_CLOSER)
            blocks.append(Block(line=start + 1, code=_code(body, start), kind="setup"))
        index += 1
    return blocks if runnable else None


def _collect_fence(lines: list[str], index: int, opener: str) -> tuple[list[str], int]:
    """Collect the lines up to the fence closing ``opener``, and that index."""
    closer = re.compile(rf"^[ \t]*{re.escape(opener[0])}{{{len(opener)},}}[ \t]*$")
    body: list[str] = []
    while index < len(lines) and not closer.match(lines[index]):
        body.append(lines[index])
        index += 1
    return body, index


def _collect_until(lines: list[str], index: int, closer: str) -> tuple[list[str], int]:
    """Collect the lines up to the one equal to ``closer``, and that index."""
    body: list[str] = []
    while index < len(lines) and lines[index].strip() != closer:
        body.append(lines[index])
        index += 1
    return body, index


def _code(body: list[str], opener_index: int) -> str:
    """Dedent the block and pad it so its line numbers match the page's."""
    return "\n" * (opener_index + 1) + dedent("\n".join(body)) + "\n"


def runnable_pages(root: Path) -> list[Path]:
    """Return every Markdown page under ``root`` marked runnable, sorted."""
    return sorted(page for page in root.rglob("*.md") if parse_page(page) is not None)


def run_page(path: Path) -> dict[str, object]:
    """Run the page's setup blocks, then its other blocks, in one namespace.

    Each page gets a fresh module (registered in ``sys.modules`` while it runs,
    so Pydantic and dataclasses can resolve annotations) and one event loop
    shared by its blocks; top-level ``await`` is allowed. Returns the namespace.
    Raises :class:`DocsExampleError` naming the page and the failing block's
    starting line.
    """
    blocks = parse_page(path)
    if blocks is None:
        raise DocsExampleError(f"{path}: not marked runnable ({RUNNABLE_MARKER})")
    ordered = [b for b in blocks if b.kind == "setup"]
    ordered += [b for b in blocks if b.kind == "run"]

    name = "docs_example_" + re.sub(r"\W", "_", path.stem)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    try:
        with asyncio.Runner() as runner:
            for block in ordered:
                _run_block(path, block, module.__dict__, runner)
    finally:
        sys.modules.pop(name, None)
    return module.__dict__


def _run_block(
    path: Path, block: Block, namespace: dict[str, object], runner: asyncio.Runner
) -> None:
    try:
        code = compile(
            block.code,
            str(path),
            "exec",
            flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
            dont_inherit=True,
        )
        result = eval(code, namespace)
        if code.co_flags & inspect.CO_COROUTINE:
            runner.run(result)
    except Exception as exc:
        what = "setup block" if block.kind == "setup" else "python block"
        raise DocsExampleError(
            f"{path}:{block.line}: {what} failed with {type(exc).__name__}: {exc}"
        ) from exc
