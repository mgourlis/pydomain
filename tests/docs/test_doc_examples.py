"""Tests for the docs example runner (tests/docs/doc_examples.py)."""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import traceback
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from textwrap import dedent

import pytest

from tests.docs.doc_examples import (
    DocsExampleError,
    run_page,
    run_tests,
    runnable_pages,
)

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


# ── Guard: a runnable page runs what it defines (DCE-126, decision log P-19) ──


def test_uncalled_test_function_fails_the_page(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "uncalled.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python
        def test_example() -> None:
            raise AssertionError("never runs")
        ~~~
        """,
    )

    with pytest.raises(DocsExampleError, match=r"uncalled\.md: .*test_example"):
        run_page(page)


def test_uncalled_main_fails_the_page(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "main.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python
        async def main() -> None:
            raise AssertionError("never runs")


        if __name__ == "__main__":
            import asyncio

            asyncio.run(main())
        ~~~
        """,
    )

    with pytest.raises(DocsExampleError, match=r"main\.md: .*\bmain\b"):
        run_page(page)


def test_uncalled_method_of_a_test_class_fails_the_page(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "test_class.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python
        class TestOrders:
            async def test_called(self) -> None:
                pass

            async def test_forgotten(self) -> None:
                raise AssertionError("never runs")
        ~~~

        <!-- docs-test: check
        await TestOrders().test_called()
        -->
        """,
    )

    with pytest.raises(DocsExampleError, match=r"TestOrders\.test_forgotten") as info:
        run_page(page)

    assert "test_called" not in str(info.value)


def test_fixture_the_check_block_rebuilds_by_hand_fails_the_page(
    tmp_path: Path,
) -> None:
    page = _page(
        tmp_path,
        "fixture.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python
        import pytest


        class TestBasket:
            @pytest.fixture
            def basket(self) -> list[str]:
                return undefined_name  # a mistake only running it shows

            async def test_starts_empty(self, basket: list[str]) -> None:
                assert basket == []
        ~~~

        <!-- docs-test: check
        await TestBasket().test_starts_empty([])
        -->
        """,
    )

    with pytest.raises(DocsExampleError, match=r"TestBasket\.basket"):
        run_page(page)


def test_tests_and_fixtures_run_through_run_tests_pass_the_guard(
    tmp_path: Path,
) -> None:
    page = _page(
        tmp_path,
        "run_tests.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python
        import pytest

        calls = []


        @pytest.fixture
        def shelf() -> list[str]:
            calls.append("shelf")
            return []


        class TestBasket:
            @pytest.fixture
            def basket(self, shelf: list[str]) -> list[str]:
                calls.append("basket")
                return shelf

            async def test_starts_empty(self, basket: list[str]) -> None:
                calls.append("test_starts_empty")
                assert basket == []


        def test_shelf(shelf: list[str]) -> None:
            calls.append("test_shelf")
        ~~~

        <!-- docs-test: check
        from tests.docs.doc_examples import run_tests

        await run_tests(TestBasket, test_shelf)
        -->
        """,
    )

    assert run_page(page)["calls"] == [
        "shelf",
        "basket",
        "test_starts_empty",
        "shelf",
        "test_shelf",
    ]


def test_mistake_in_a_fixture_run_through_run_tests_fails_the_page(
    tmp_path: Path,
) -> None:
    page = _page(
        tmp_path,
        "broken_fixture.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python
        import pytest


        class TestBasket:
            @pytest.fixture
            def basket(self) -> list[str]:
                return undefined_name

            async def test_starts_empty(self, basket: list[str]) -> None:
                assert basket == []
        ~~~

        <!-- docs-test: check
        from tests.docs.doc_examples import run_tests

        await run_tests(TestBasket)
        -->
        """,
    )

    with pytest.raises(
        DocsExampleError,
        match=r"broken_fixture\.md:16: check block failed with NameError",
    ):
        run_page(page)


def test_code_defined_elsewhere_is_not_required_to_run(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "imported.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python
        from tests.docs.test_doc_examples import (
            test_blocks_run_in_order_in_one_namespace as test_imported,
        )
        from pydomain.testing.contracts import CheckpointStoreContract


        class TestInherited(CheckpointStoreContract):
            pass
        ~~~
        """,
    )

    run_page(page)


def test_function_called_in_a_visible_block_passes_the_guard(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "visible_call.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python
        def main() -> str:
            return "ran"


        result = main()
        ~~~
        """,
    )

    assert run_page(page)["result"] == "ran"


def test_unrun_definitions_are_named_in_page_order(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "several.md",
        """\
        <!-- docs-test: runnable -->

        ~~~python
        def test_first() -> None: ...


        def main() -> None: ...


        def helper() -> None: ...
        ~~~
        """,
    )

    with pytest.raises(
        DocsExampleError, match=r"several\.md: never runs test_first, main;"
    ):
        run_page(page)


# ── Clean-up after a page, even a failing one (DCE-126, decision log P-19) ──


def test_module_aliases_of_a_failing_page_are_removed(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "aliases.md",
        """\
        <!-- docs-test: runnable -->

        <!-- docs-test: setup
        import sys

        sys.modules["docs_alias_synthetic_recipe"] = sys.modules[__name__]
        -->

        ~~~python
        raise ValueError("boom")
        ~~~
        """,
    )

    try:
        with pytest.raises(DocsExampleError):
            run_page(page)

        assert "docs_alias_synthetic_recipe" not in sys.modules
    finally:
        sys.modules.pop("docs_alias_synthetic_recipe", None)


def test_logging_handlers_of_a_failing_page_are_removed(tmp_path: Path) -> None:
    logger = logging.getLogger("docs_example_synthetic.saga")
    page = _page(
        tmp_path,
        "handlers.md",
        """\
        <!-- docs-test: runnable -->

        <!-- docs-test: setup
        import logging

        logging.getLogger("docs_example_synthetic.saga").addHandler(
            logging.NullHandler()
        )
        logging.getLogger().addHandler(logging.NullHandler())
        -->

        ~~~python
        raise ValueError("boom")
        ~~~
        """,
    )
    root_handlers = list(logging.getLogger().handlers)

    try:
        with pytest.raises(DocsExampleError):
            run_page(page)

        assert logger.handlers == []
        assert logging.getLogger().handlers == root_handlers
    finally:
        logger.handlers.clear()
        for handler in logging.getLogger().handlers:
            if handler not in root_handlers:
                logging.getLogger().removeHandler(handler)


# ── run_tests: a page's pytest tests with their fixtures ──────────────────


def test_run_tests_gives_each_test_a_new_instance_and_fresh_fixtures() -> None:
    seen: list[tuple[int, int]] = []

    class TestPair:
        @pytest.fixture
        def items(self) -> list[int]:
            return []

        def test_one(self, items: list[int]) -> None:
            items.append(1)
            seen.append((id(self), len(items)))

        def test_two(self, items: list[int]) -> None:
            items.append(2)
            seen.append((id(self), len(items)))

    asyncio.run(run_tests(TestPair))

    assert [length for _, length in seen] == [1, 1]
    assert seen[0][0] != seen[1][0]


def test_run_tests_shares_a_fixture_within_one_test() -> None:
    created: list[object] = []

    class TestShared:
        @pytest.fixture
        def store(self) -> object:
            created.append(object())
            return created[-1]

        @pytest.fixture
        def reader(self, store: object) -> object:
            return store

        def test_same(self, store: object, reader: object) -> None:
            assert store is reader

    asyncio.run(run_tests(TestShared))

    assert len(created) == 1


def test_run_tests_tears_down_yield_fixtures_newest_first_after_a_failure() -> None:
    log: list[str] = []

    class TestTeardown:
        @pytest.fixture
        def outer(self) -> Iterator[str]:
            log.append("outer up")
            yield "outer"
            log.append("outer down")

        @pytest.fixture
        async def inner(self, outer: str) -> AsyncIterator[str]:
            log.append("inner up")
            yield "inner"
            log.append("inner down")

        async def test_fails(self, inner: str) -> None:
            log.append("test")
            raise AssertionError("synthetic")

    with pytest.raises(AssertionError, match="synthetic"):
        asyncio.run(run_tests(TestTeardown))

    assert log == ["outer up", "inner up", "test", "inner down", "outer down"]


def test_run_tests_awaits_async_fixtures_and_tests() -> None:
    results: list[int] = []

    class TestAsync:
        @pytest.fixture
        async def answer(self) -> int:
            await asyncio.sleep(0)
            return 42

        async def test_answer(self, answer: int) -> None:
            results.append(answer)

    asyncio.run(run_tests(TestAsync))

    assert results == [42]


def test_run_tests_runs_autouse_fixtures_and_honours_fixture_names() -> None:
    log: list[str] = []

    class TestNamed:
        @pytest.fixture(autouse=True)
        def always(self) -> None:
            log.append("autouse")

        @pytest.fixture(name="store")
        def make_store(self) -> str:
            return "store"

        def test_named(self, store: str) -> None:
            log.append(store)

    asyncio.run(run_tests(TestNamed))

    assert log == ["autouse", "store"]


def test_run_tests_prefers_the_class_fixture_over_the_module_one() -> None:
    namespace: dict[str, object] = {"pytest": pytest}
    exec(  # a function's module globals are where run_tests finds its fixtures
        dedent(
            """\
            @pytest.fixture
            def name() -> str:
                return "module"


            class TestOverride:
                @pytest.fixture
                def name(self) -> str:
                    return "class"

                def test_name(self, name: str) -> None:
                    assert name == "class"


            def test_module_name(name: str) -> None:
                assert name == "module"
            """
        ),
        namespace,
    )

    asyncio.run(
        run_tests(namespace["TestOverride"], namespace["test_module_name"])  # type: ignore[arg-type]
    )


def test_run_tests_names_a_missing_fixture() -> None:
    class TestMissing:
        def test_needs(self, tmp_path: Path) -> None: ...

    with pytest.raises(LookupError, match=r"test_needs needs a fixture 'tmp_path'"):
        asyncio.run(run_tests(TestMissing))


def test_run_tests_rejects_a_class_without_tests() -> None:
    class TestNothing:
        pass

    with pytest.raises(LookupError, match="TestNothing has no test_\\* methods"):
        asyncio.run(run_tests(TestNothing))


def test_run_tests_rejects_a_fixture_that_yields_twice() -> None:
    class TestTwice:
        @pytest.fixture
        def twice(self) -> Iterator[int]:
            yield 1
            yield 2

        def test_uses(self, twice: int) -> None: ...

    with pytest.raises(RuntimeError, match="yielded more than once"):
        asyncio.run(run_tests(TestTwice))
