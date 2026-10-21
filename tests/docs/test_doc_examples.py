"""Tests for the docs example runner (tests/docs/doc_examples.py)."""

from __future__ import annotations

import asyncio
import re
import sys
import traceback
from pathlib import Path
from textwrap import dedent

import pytest

from tests.docs.doc_examples import DocsExampleError, run_page, runnable_pages

FENCE = "```"


def _page(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(dedent(body).replace("~~~", FENCE), encoding="utf-8")
    return path


def _raw_page(tmp_path: Path, name: str, text: str) -> Path:
    """Write ``text`` byte for byte: no dedent, no fence or newline rewriting."""
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return path


def test_blocks_run_in_order_in_one_namespace(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "page.md",
        """\
        # Page
        <!-- docs-test: runnable -->

        ~~~python
        total = 1
        ~~~

        Some prose.

        ~~~python
        total += 1
        ~~~
        """,
    )

    namespace = run_page(page)

    assert namespace["total"] == 2


def test_failing_block_reports_page_path_and_starting_line(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "failing.md",
        """\
        # Failing
        <!-- docs-test: runnable -->

        ~~~python
        ok = True
        ~~~

        ~~~python
        raise ValueError("boom")
        ~~~
        """,
    )

    with pytest.raises(
        DocsExampleError, match=rf"{re.escape(str(page))}:8: .*ValueError: boom"
    ):
        run_page(page)


def test_traceback_points_at_the_markdown_line(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "traceback.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python
        x = 1

        y = 1 / 0
        ~~~
        """,
    )

    with pytest.raises(DocsExampleError) as info:
        run_page(page)

    assert info.value.__cause__ is not None
    frame = traceback.extract_tb(info.value.__cause__.__traceback__)[-1]
    assert (frame.filename, frame.lineno) == (str(page), 6)


def test_skipped_block_is_not_run(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "skip.md",
        """\
        <!-- docs-test: runnable -->

        <!-- docs-test: skip -->
        ~~~python
        raise RuntimeError("must not run")
        ~~~

        ~~~python
        after_skip = True
        ~~~
        """,
    )

    assert run_page(page)["after_skip"] is True


def test_setup_block_runs_before_visible_blocks(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "setup.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python
        assert helper() == 42
        ~~~

        <!-- docs-test: setup
        def helper() -> int:
            return 42
        -->
        """,
    )

    assert run_page(page)["helper"] is not None


def test_non_python_fences_are_ignored(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "bash.md",
        """\
        <!-- docs-test: runnable -->

        ~~~bash
        pip install pydomain-lib
        ~~~

        ~~~python
        ran = True
        ~~~
        """,
    )

    assert run_page(page)["ran"] is True


def test_top_level_await_runs(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "await.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python
        import asyncio

        async def answer() -> int:
            await asyncio.sleep(0)
            return 42

        result = await answer()
        ~~~
        """,
    )

    assert run_page(page)["result"] == 42


def test_blocks_of_a_page_share_one_event_loop(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "loop.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python
        import asyncio

        await asyncio.sleep(0)
        first_loop = asyncio.get_running_loop()
        ~~~

        ~~~python
        await asyncio.sleep(0)
        same_loop = asyncio.get_running_loop() is first_loop
        ~~~
        """,
    )

    assert run_page(page)["same_loop"] is True


def test_pages_do_not_share_a_namespace(tmp_path: Path) -> None:
    body = """\
        <!-- docs-test: runnable -->

        ~~~python
        assert "leaked" not in globals()
        leaked = True
        ~~~
        """
    first = _page(tmp_path, "first.md", body)
    second = _page(tmp_path, "second.md", body)

    run_page(first)
    run_page(second)


def test_unmarked_page_is_ignored(tmp_path: Path) -> None:
    marked = _page(
        tmp_path,
        "marked.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python
        pass
        ~~~
        """,
    )
    _page(
        tmp_path,
        "unmarked.md",
        """\
        ~~~python
        raise RuntimeError("not runnable")
        ~~~
        """,
    )

    assert runnable_pages(tmp_path) == [marked]


def test_markers_and_blocks_inside_another_fence_are_ignored(tmp_path: Path) -> None:
    _page(
        tmp_path,
        "shown.md",
        """\
        ````markdown
        <!-- docs-test: runnable -->

        ~~~python
        raise RuntimeError("only shown")
        ~~~
        ````
        """,
    )

    assert runnable_pages(tmp_path) == []


def test_runnable_page_without_python_blocks_fails(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "empty.md",
        """\
        <!-- docs-test: runnable -->

        Only prose.
        """,
    )

    with pytest.raises(DocsExampleError, match="runnable page has no python blocks"):
        run_page(page)


def test_runnable_page_with_only_skipped_blocks_fails(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "all_skipped.md",
        """\
        <!-- docs-test: runnable -->

        <!-- docs-test: skip -->
        ~~~python
        pass
        ~~~
        """,
    )

    with pytest.raises(DocsExampleError, match="runnable page has no python blocks"):
        run_page(page)


def test_backticks_in_prose_do_not_open_a_fence(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "inline.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python~~~ is shown inline here.

        ~~~python
        after = 1
        ~~~
        """,
    )

    assert run_page(page).get("after") == 1


def test_block_inside_an_html_comment_is_ignored(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "comment.md",
        """\
        <!-- docs-test: runnable -->

        <!--
        ~~~python
        commented = True
        ~~~
        -->

        ~~~python
        ran = True
        ~~~
        """,
    )

    assert "commented" not in run_page(page)


def test_fence_in_an_indented_code_block_is_ignored(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "indented.md",
        """\
        <!-- docs-test: runnable -->

            ~~~python
            indented = True
            ~~~

        ~~~python
        ran = True
        ~~~
        """,
    )

    assert "indented" not in run_page(page)


def test_tilde_fences_run(tmp_path: Path) -> None:
    page = _raw_page(
        tmp_path,
        "tilde.md",
        "<!-- docs-test: runnable -->\n\n~~~python\nran = True\n~~~\n",
    )

    assert run_page(page)["ran"] is True


def test_tilde_fence_info_string_may_contain_backticks(tmp_path: Path) -> None:
    page = _raw_page(
        tmp_path,
        "tilde_info.md",
        "<!-- docs-test: runnable -->\n\n~~~python `x`\nran = True\n~~~\n",
    )

    assert run_page(page)["ran"] is True


def test_crlf_page_runs_and_reports_the_line(tmp_path: Path) -> None:
    page = _raw_page(
        tmp_path,
        "crlf.md",
        "<!-- docs-test: runnable -->\r\n\r\n"
        "~~~python\r\nok = True\r\n~~~\r\n\r\n"
        "~~~python\r\nraise ValueError('boom')\r\n~~~\r\n",
    )

    with pytest.raises(DocsExampleError, match=rf"{re.escape(str(page))}:7: "):
        run_page(page)


def test_python_fence_with_attributes_runs(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "title.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python title="example.py"
        ran = True
        ~~~
        """,
    )

    assert run_page(page)["ran"] is True


def test_fences_indented_inside_list_items_run(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "list.md",
        """\
        <!-- docs-test: runnable -->

        1. First step:

           ~~~python
           steps = 1
           if steps:
               steps += 1
           ~~~

           - A nested item:

             ~~~python
             nested = steps
             ~~~
        """,
    )

    assert run_page(page)["nested"] == 2


def test_event_loop_is_closed_after_the_page(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "closed.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python
        import asyncio

        await asyncio.sleep(0)
        loop = asyncio.get_running_loop()
        ~~~
        """,
    )

    loop = run_page(page)["loop"]

    assert isinstance(loop, asyncio.AbstractEventLoop)
    assert loop.is_closed()


def test_no_page_module_is_left_in_sys_modules(tmp_path: Path) -> None:
    passing = _page(
        tmp_path,
        "passing.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python
        pass
        ~~~
        """,
    )
    failing = _page(
        tmp_path,
        "failing.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python
        raise ValueError("boom")
        ~~~
        """,
    )

    run_page(passing)
    with pytest.raises(DocsExampleError):
        run_page(failing)

    assert not [name for name in sys.modules if name.startswith("docs_example_")]


def test_check_block_runs_in_page_order(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "check.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python
        calls = []

        def test_example() -> None:
            calls.append("ran")
        ~~~

        <!-- docs-test: check
        test_example()
        -->

        ~~~python
        assert calls == ["ran"]
        ~~~
        """,
    )

    assert run_page(page)["calls"] == ["ran"]


def test_check_block_may_await(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "check_await.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python
        async def test_example() -> int:
            return 7
        ~~~

        <!-- docs-test: check
        result = await test_example()
        -->
        """,
    )

    assert run_page(page)["result"] == 7


def test_failing_check_block_reports_its_line(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "check_fails.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python
        def test_example() -> None:
            raise AssertionError("synthetic")
        ~~~

        <!-- docs-test: check
        test_example()
        -->
        """,
    )

    with pytest.raises(
        DocsExampleError, match=r"check_fails\.md:8: check block failed"
    ):
        run_page(page)
