# How to Add a Runnable Docs Example

> **Prerequisites:** a clone of the pydomain repository with the `dev` extra installed (`make install`). This guide is for contributors to pydomain's own documentation: the runner lives in the repository's `tests/docs/` and is not part of the `pydomain` package.

## Problem

A Diataxis page shows Python examples. Nothing checks that they still match the library, so a page can keep teaching an attribute that was renamed or a behaviour that was fixed.

## Solution

Mark the page runnable. `tests/docs/test_diataxis_examples.py` finds every page under `docs/diataxis/` marked runnable and runs its fenced `python` blocks from top to bottom, so `make test` fails when a page stops matching the library. The parser and runner are in `tests/docs/doc_examples.py`.

Markers are HTML comments, so they do not show when the page is rendered:

| Marker | Effect |
|--------|--------|
| `<!-- docs-test: runnable -->` | The page's `python` blocks run as one test |
| `<!-- docs-test: skip -->` | The next fenced block does not run |
| `<!-- docs-test: setup` … `-->` | Hidden code that runs before the first block, e.g. imports the page leaves out |
| `<!-- docs-test: check` … `-->` | Hidden code that runs in page order, e.g. a call to the test functions or the `main()` a block above defines, or `run_tests()` for tests that take fixtures |

## Steps

### 1. Mark the page runnable

Put the marker near the top of the page, after the title:

````markdown
# How to Create a Value Object

<!-- docs-test: runnable -->
````

### 2. Add the setup the page leaves out

If the blocks rely on imports or names the page does not show, put them in a setup comment. It runs once, before the first block:

````markdown
<!-- docs-test: setup
from uuid import uuid4
-->
````

### 3. Call what the blocks only define

A block that defines test functions or a `main()` without calling them proves only that they compile, so the page fails until something runs them. Call them from a check comment after the block; it runs in page order and may use `await`:

````markdown
<!-- docs-test: check
await test_publish_order()
-->
````

When the tests take pytest fixtures, run them with `run_tests()` from `tests/docs/doc_examples.py` instead of building the fixtures' values by hand, so the visible fixtures run as written. It takes `Test*` classes, whose `test_*` methods each run on a new instance, and test functions:

````markdown
<!-- docs-test: check
from tests.docs.doc_examples import run_tests

await run_tests(TestIntegrationEventPublishing, test_inbound_event_flow)
-->
````

`run_tests()` fills each parameter from the fixture of that name on the class, or else on the page. Fixtures may depend on other fixtures, return or `yield` a value (the code after `yield` runs after the test) and be `async`; autouse fixtures run too. pytest's built-in fixtures, such as `tmp_path` or `request`, are not available: skip a block whose fixtures need them.

### 4. Skip the fragments that cannot run

Mark a block that cannot run on its own, such as a fragment that needs a database, with `skip` on the line before its fence:

````markdown
<!-- docs-test: skip -->
```python
app = bootstrap(...)  # a fragment that cannot run on its own
```
````

### 5. Run the docs example tests

```bash
python -m pytest tests/docs -q
```

## How the Blocks Run

- **One namespace per page.** A later block sees the names an earlier one defined; pages never share names.
- **Top-level `await`.** A block may use `await` outside a function. All blocks of a page share one event loop.
- **Only `python` fences run.** `bash`, `toml` and other fences are ignored, as are markers and blocks shown inside another fence. A fence inside an ordinary HTML comment (`<!--` … `-->`) or an indented code block does not run, so commenting out a block turns it off.
- **A runnable page must run something.** A page marked runnable with no `python` block left to run, because it has none or all are skipped, fails.
- **A runnable page must run what it defines for a test.** The page fails, naming each one, if a `test_*` function, a `main()`, a `test_*` method of a `Test*` class, or a pytest fixture defined on the page never starts running. Names imported from elsewhere, such as a contract suite's inherited tests, do not count.
- **Pages clean up after themselves.** When the page ends, even after a failing block, the runner removes every `sys.modules` name the page pointed at its own module and every logging handler it added, so a failure cannot leak them into the pages and tests that run later.
- **Failures name the line.** A failing block reports the page path and the line of its opening fence, and the traceback points at the Markdown line.

Mark a page runnable only when every block runs as written. A page that shows fragments can mark them `skip` or supply the missing context in a setup comment. Examples that need third-party packages, such as a broker client, stay unmarked.

## Expected Outcome

The page appears as a case of `test_page_examples_run` in `tests/docs/test_diataxis_examples.py`, named after its path under `docs/diataxis/`. The case fails, naming the page and line, as soon as an example stops matching the library. A page that taught a defect is fixed in the same PR as the fix and marked runnable ([ADR-066](../../../adr/ADR-066-test-first-fixes-with-documentation.md)).

## See Also

- [Test Structure](../../concepts/testing/test-structure.md) — pydomain's test tree and tools
- [Testing Philosophy](../../concepts/testing/testing-philosophy.md) — why the docs examples are tested
