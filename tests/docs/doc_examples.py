"""Run the fenced Python examples of Diataxis pages marked runnable.

Not collected by pytest itself (no ``test_`` prefix); used by
``test_diataxis_examples.py`` and tested by ``test_doc_examples.py``.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import logging
import re
import sys
import types
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from typing import Any, Literal

from _pytest.fixtures import getfixturemarker

RUNNABLE_MARKER = "<!-- docs-test: runnable -->"
SKIP_MARKER = "<!-- docs-test: skip -->"
SETUP_OPENER = "<!-- docs-test: setup"
CHECK_OPENER = "<!-- docs-test: check"
COMMENT_CLOSER = "-->"

COMMENT_OPENER = "<!--"

# CommonMark: a backtick fence's info string cannot contain a backtick.
_FENCE = re.compile(r"^(?P<fence>`{3,}(?=[^`]*$)|~{3,})[ \t]*(?P<info>.*)$")
_LIST_ITEM = re.compile(r"^(?P<marker>[-*+]|\d{1,9}[.)])(?P<gap> {1,4})\S")


class DocsExampleError(Exception):
    """A block of a runnable page failed; the message names page and line."""


@dataclass(frozen=True)
class Block:
    """One Python block of a page: the line of its opening fence and its code."""

    line: int
    code: str
    kind: Literal["run", "skip", "setup", "check"]


def parse_page(path: Path) -> list[Block] | None:
    """Return the page's Python blocks, or ``None`` if it is not marked runnable.

    Markers and setup comments are only recognised outside fenced blocks, so a
    page can show them inside a ``markdown`` fence without being affected.
    Fences inside an ordinary HTML comment or an indented code block (four
    or more spaces past the enclosing list item's content) are ignored.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks: list[Block] = []
    runnable = False
    skip_next = False
    columns: list[int] = []  # content columns of the enclosing list items
    previous_blank = True
    index = 0
    while index < len(lines):
        line = lines[index].expandtabs(4)
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        fence = _FENCE.match(stripped)
        item = _LIST_ITEM.match(stripped)
        if stripped and (previous_blank or fence or item):
            while columns and indent < columns[-1]:
                columns.pop()
        code_block = indent - (columns[-1] if columns else 0) >= 4
        if code_block:
            pass
        elif fence:
            start = index
            body, index = _collect_fence(lines, index + 1, fence["fence"])
            words = fence["info"].split()
            if words and words[0] == "python":
                kind: Literal["run", "skip"] = "skip" if skip_next else "run"
                blocks.append(Block(line=start + 1, code=_code(body, start), kind=kind))
            skip_next = False
        elif item:
            columns.append(indent + len(item["marker"]) + len(item["gap"]))
        elif stripped == RUNNABLE_MARKER:
            runnable = True
        elif stripped == SKIP_MARKER:
            skip_next = True
        elif stripped in (SETUP_OPENER, CHECK_OPENER):
            start = index
            body, index = _collect_until(lines, index + 1, COMMENT_CLOSER)
            hidden: Literal["setup", "check"] = (
                "setup" if stripped == SETUP_OPENER else "check"
            )
            blocks.append(Block(line=start + 1, code=_code(body, start), kind=hidden))
        elif stripped.startswith(COMMENT_OPENER) and COMMENT_CLOSER not in stripped:
            while index < len(lines) and COMMENT_CLOSER not in lines[index]:
                index += 1
        previous_blank = not stripped
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

    Visible blocks and hidden ``check`` comments run in page order, so a check
    can call the test functions the blocks above it define.

    Each page gets a fresh module (registered in ``sys.modules`` while it runs,
    so Pydantic and dataclasses can resolve annotations) and one event loop
    shared by its blocks; top-level ``await`` is allowed. Afterwards, even
    when a block failed, every other ``sys.modules`` name the page pointed
    at its module and every logging handler it added are removed.

    The page must run everything it defines for a test: each ``test_*``
    function, ``main()``, ``test_*`` method of a ``Test*`` class and pytest
    fixture whose code is on the page (see :func:`unrun_definitions`).
    Returns the namespace.

    Raises :class:`DocsExampleError` naming the page and the failing block's
    starting line, the page and what it never ran, or the page alone if it
    has no block to run.
    """
    blocks = parse_page(path)
    if blocks is None:
        raise DocsExampleError(f"{path}: not marked runnable ({RUNNABLE_MARKER})")
    ordered = [b for b in blocks if b.kind == "setup"]
    ordered += [b for b in blocks if b.kind in ("run", "check")]
    if not any(b.kind == "run" for b in blocks):
        raise DocsExampleError(f"{path}: runnable page has no python blocks to run")

    name = "docs_example_" + re.sub(r"\W", "_", path.stem)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    try:
        with _restored_logging(), _started_code(str(path)) as started:
            with asyncio.Runner() as runner:
                for block in ordered:
                    _run_block(path, block, module.__dict__, runner)
    finally:
        for alias in [key for key, value in sys.modules.items() if value is module]:
            del sys.modules[alias]
    unrun = unrun_definitions(module.__dict__, str(path), started)
    if unrun:
        raise DocsExampleError(
            f"{path}: never runs {', '.join(unrun)}; call each from a "
            f"{CHECK_OPENER} --> block, through run_tests() if it takes fixtures"
        )
    return module.__dict__


def unrun_definitions(
    namespace: dict[str, object], filename: str, started: set[types.CodeType]
) -> list[str]:
    """Name what the page defines for a test but never started running.

    Looks at the ``test_*`` functions and ``main`` in *namespace*, the
    ``test_*`` methods of its ``Test*`` classes (inherited ones and nested
    ``Test*`` classes too, as pytest collects them) and the pytest fixtures
    of the namespace and its classes, counting only code compiled from
    *filename*.
    """
    unrun: list[str] = []

    def check(label: str, function: object) -> None:
        function = inspect.unwrap(function)  # type: ignore[arg-type]
        code = getattr(function, "__code__", None)
        if code is None or code.co_filename != filename:
            return
        if code not in started:
            unrun.append(label)

    def visit_class(label: str, cls: type) -> None:
        for attribute, member in _class_attributes(cls).items():
            name = f"{label}.{attribute}"
            if getfixturemarker(member) is not None:
                check(name, _wrapped_fixture(member))
            elif not label.split(".")[-1].startswith("Test"):
                continue
            elif attribute.startswith("test_"):
                check(name, getattr(member, "__func__", member))
            elif inspect.isclass(member) and attribute.startswith("Test"):
                visit_class(name, member)

    for key, value in namespace.items():
        if getfixturemarker(value) is not None:
            check(key, _wrapped_fixture(value))
        elif inspect.isfunction(value) and (key.startswith("test_") or key == "main"):
            check(key, value)
        elif inspect.isclass(value) and value.__module__ == namespace.get("__name__"):
            visit_class(key, value)
    return unrun


@contextmanager
def _started_code(filename: str) -> Iterator[set[types.CodeType]]:
    """Collect the code objects from *filename* that start running meanwhile."""
    monitoring = sys.monitoring
    # 3 and 4 have no reserved meaning; 0 to 2 are the debugger's, coverage's
    # and the profiler's ids, and 5 the optimizer's.
    preferred = (3, 4, monitoring.PROFILER_ID, monitoring.COVERAGE_ID)
    tool = next(
        (
            i
            for i in (*preferred, monitoring.DEBUGGER_ID)
            if monitoring.get_tool(i) is None
        ),
        None,
    )
    if tool is None:
        raise RuntimeError("no free sys.monitoring tool id for the docs runner")
    started: set[types.CodeType] = set()

    def on_start(code: types.CodeType, offset: int) -> None:
        if code.co_filename == filename:
            started.add(code)

    monitoring.use_tool_id(tool, "pydomain-docs-runner")
    try:
        monitoring.register_callback(tool, monitoring.events.PY_START, on_start)
        monitoring.set_events(tool, monitoring.events.PY_START)
        yield started
    finally:
        monitoring.set_events(tool, monitoring.events.NO_EVENTS)
        monitoring.register_callback(tool, monitoring.events.PY_START, None)
        monitoring.free_tool_id(tool)


@contextmanager
def _restored_logging() -> Iterator[None]:
    """Restore every logger's handlers and level as they were before.

    Covers a page that adds handlers or sets levels, and one that calls
    ``logging.basicConfig(force=True)``, which removes the root handlers.
    Loggers the page created lose their handlers and go back to ``NOTSET``.
    """

    def loggers() -> list[logging.Logger]:
        found = [logging.getLogger()]
        found += [
            logger
            for logger in logging.Logger.manager.loggerDict.values()
            if isinstance(logger, logging.Logger)
        ]
        return found

    before = {id(logger): (list(logger.handlers), logger.level) for logger in loggers()}
    try:
        yield
    finally:
        for logger in loggers():
            handlers, level = before.get(id(logger), ([], logging.NOTSET))
            for handler in list(logger.handlers):
                if handler not in handlers:
                    logger.removeHandler(handler)
            for handler in handlers:
                if handler not in logger.handlers:
                    logger.addHandler(handler)
            logger.setLevel(level)


# ── Running a page's pytest tests with their fixtures ──────────────────────


async def run_tests(*targets: type | Callable[..., Any]) -> None:
    """Run test functions and ``Test*`` classes the way pytest would, simply.

    For a check block: ``await run_tests(TestOrders)`` runs every ``test_*``
    method of the class, each on a new instance; ``await run_tests(test_x)``
    runs one function. Each parameter is filled from a pytest fixture of the
    same name, looked up on the class first and then in the function's
    module, so the page's visible fixtures run as written. Fixtures may
    depend on fixtures, return, ``yield`` (teardown runs after the test,
    newest first) or be ``async``; each is set up once per test. Autouse
    fixtures run too. Built-in fixtures such as ``tmp_path`` are not
    available: a parameter without a fixture raises ``LookupError``.
    """
    for target in targets:
        if inspect.isclass(target):
            names = [
                name for name in _class_attributes(target) if name.startswith("test_")
            ]
            if not names:
                raise LookupError(f"{target.__qualname__} has no test_* methods")
            for name in names:
                instance = target()
                await _run_test(getattr(instance, name), instance)
        else:
            await _run_test(target, None)


def _class_attributes(cls: type) -> dict[str, Any]:
    """The class's attributes, base classes first, in definition order."""
    attributes: dict[str, Any] = {}
    for klass in reversed(cls.__mro__):
        attributes.update(vars(klass))
    return attributes


def _wrapped_fixture(fixture: Any) -> Callable[..., Any]:
    """The function a pytest fixture decorator wraps."""
    unwrap = getattr(fixture, "_get_wrapped_function", None)
    if unwrap is not None:  # pytest >= 8.4
        return unwrap()  # type: ignore[no-any-return]
    return fixture.__pytest_wrapped__.obj  # type: ignore[no-any-return]


def _fixtures_for(
    test: Callable[..., Any], instance: object
) -> dict[str, Callable[..., Any]]:
    """Fixture functions visible to *test*, by name; the class's win."""
    function = getattr(test, "__func__", test)
    sources: list[tuple[dict[str, Any], object]] = [(function.__globals__, None)]
    if instance is not None:
        sources.append((_class_attributes(type(instance)), instance))
    fixtures: dict[str, Callable[..., Any]] = {}
    for attributes, owner in sources:
        for attribute, value in attributes.items():
            marker = getfixturemarker(value)
            if marker is None:
                continue
            wrapped = _wrapped_fixture(value)
            if owner is not None:
                wrapped = wrapped.__get__(owner)
            fixtures[marker.name or attribute] = wrapped
    return fixtures


def _autouse(test: Callable[..., Any], instance: object) -> list[str]:
    function = getattr(test, "__func__", test)
    sources: list[dict[str, Any]] = [function.__globals__]
    if instance is not None:
        sources.append(_class_attributes(type(instance)))
    names: list[str] = []
    for attributes in sources:
        for attribute, value in attributes.items():
            marker = getfixturemarker(value)
            if marker is not None and marker.autouse:
                names.append(marker.name or attribute)
    return names


async def _run_test(test: Callable[..., Any], instance: object) -> None:
    fixtures = _fixtures_for(test, instance)
    values: dict[str, Any] = {}
    teardowns: list[Any] = []

    async def resolve(name: str, wanted_by: str) -> Any:
        if name in values:
            return values[name]
        fixture = fixtures.get(name)
        if fixture is None:
            raise LookupError(f"{wanted_by} needs a fixture {name!r}; none is defined")
        arguments = {
            parameter: await resolve(parameter, name)
            for parameter in _fixture_names(fixture)
        }
        result = fixture(**arguments)
        if inspect.isasyncgen(result):
            value = await anext(result)
        elif inspect.isgenerator(result):
            value = next(result)
        elif inspect.isawaitable(result):
            value = await result
        else:
            value = result
        if inspect.isasyncgen(result) or inspect.isgenerator(result):
            teardowns.append(result)
        values[name] = value
        return value

    label = getattr(test, "__qualname__", repr(test))
    try:
        for name in _autouse(test, instance):
            await resolve(name, label)
        arguments = {
            parameter: await resolve(parameter, label)
            for parameter in _fixture_names(test)
        }
        result = test(**arguments)
        if inspect.isawaitable(result):
            await result
    except BaseException as failure:
        errors = await _tear_down(teardowns)
        if errors:
            failure.add_note(
                "Teardown also failed: "
                + "; ".join(f"{type(e).__name__}: {e}" for e in errors)
            )
        raise
    errors = await _tear_down(teardowns)
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise ExceptionGroup(f"{label}: several fixture teardowns failed", errors)


def _fixture_names(function: Callable[..., Any]) -> list[str]:
    """The parameters pytest fills from fixtures: those without a default."""
    return [
        name
        for name, parameter in inspect.signature(function).parameters.items()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    ]


async def _tear_down(teardowns: list[Any]) -> list[Exception]:
    """Run every teardown, newest first, and return the errors they raised."""
    errors: list[Exception] = []
    for generator in reversed(teardowns):
        try:
            await _finish(generator)
        except Exception as error:
            errors.append(error)
    return errors


async def _finish(generator: Any) -> None:
    """Run a yield fixture's teardown; it must not yield again."""
    try:
        if inspect.isasyncgen(generator):
            await anext(generator)
        else:
            next(generator)
    except (StopIteration, StopAsyncIteration):
        return
    raise RuntimeError(f"fixture {generator.__qualname__} yielded more than once")


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
        what = {"setup": "setup block", "check": "check block"}.get(
            block.kind, "python block"
        )
        raise DocsExampleError(
            f"{path}:{block.line}: {what} failed with {type(exc).__name__}: {exc}"
        ) from exc
