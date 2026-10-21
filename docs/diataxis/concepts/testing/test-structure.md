# Test Structure

> **Adoption Level:** All levels
> **Prerequisite:** [Testing Philosophy](testing-philosophy.md)

This page describes how pydomain's own test suite is organized and the tools it uses, then how to organize the tests of a project that uses pydomain.

## pydomain's Test Tree

Five folders under `tests/` mirror the packages under `src/pydomain/`: `ddd/`, `cqrs/` (with `cqrs/saga/`), `es/`, `infrastructure/` and `testing/`. Four more do not: `saga/` holds most saga tests, and `docs/`, `tooling/` and `support/` hold the docs examples, the tooling tests and the test helpers:

```
tests/
├── conftest.py               # anyio_backend (asyncio), leaked-task check, Hypothesis profile
├── test_architecture.py      # Layer rules (pytest-archon)
├── test_package_exports.py   # __all__ of every public package
├── ddd/                      # pydomain.ddd
├── cqrs/                     # pydomain.cqrs
│   └── saga/                 # Mirrors pydomain.cqrs.saga: a smaller unit set
├── saga/                     # Most saga tests: unit suites and cross-component behaviour
├── es/                       # pydomain.es
├── infrastructure/           # pydomain.infrastructure
├── testing/                  # pydomain.testing and pydomain.testing.contracts
├── docs/                     # Docs example runner and its tests
├── tooling/                  # The commit-msg hook
└── support/                  # Test helpers and their tests
    └── probes/               # Audit verification scripts; not collected
```

Inside a package folder, most files cover one class or concept: `tests/ddd/test_entity.py`, `tests/cqrs/test_unit_of_work.py`, `tests/es/test_upcasting.py`. `cqrs/`, `es/`, `saga/` and `infrastructure/` each have a `conftest.py` with the synthetic domain types and fixtures their tests share.

Sagas have two folders. `tests/saga/` holds most saga tests: unit suites for the saga, its state, the manager, the registry, hydration and the exceptions (`test_saga.py`, `test_state.py`, `test_manager.py`, `test_registry.py`, `test_hydrate.py`, `test_exceptions.py`), plus behaviour that spans them: whole flows, durability, recovery, interruption, resume, failure, timeouts, pruning and tracing. `tests/cqrs/saga/` mirrors the `pydomain.cqrs.saga` package with a smaller unit set (`test_saga.py`, `test_state.py`, `test_manager.py`, `test_registry.py`) plus `test_fake_saga_repository.py`. A new saga test goes in `tests/saga/`, next to the existing suite for the class it tests.

`tests/testing/` runs every contract suite against its shipped adapter (`test_contracts_*.py`), tests the suites' own behaviour (backend pinning, fixture styles, packaging) and holds fake-specific tests.

## Async Tests

pydomain is async throughout, and its tests use the pytest plugin that ships with `anyio`. An async test opts in with a marker:

```python
import pytest


@pytest.mark.anyio
async def test_commit_publishes_collected_events() -> None:
    ...
```

A module whose tests are all async can set `pytestmark = pytest.mark.anyio` once instead. `tests/conftest.py` defines the `anyio_backend` fixture, which returns `"asyncio"`, so every test runs on asyncio.

There is no `asyncio_mode` setting in `pyproject.toml`. That option belongs to `pytest-asyncio`, which pydomain does not use. pytest 8.4 and later fail an `async def` test that has no `anyio` marker, because no installed plugin claims it.

The root `conftest.py` also runs a **leaked-task check** around every anyio test: a test that leaves a pending asyncio task behind fails, with the task's name in the message. A test that leaves one on purpose opts out with `@pytest.mark.allow_leaked_tasks(reason="DCE-NN …")`. Sync tests that drive their own loop, such as the docs example tests, are not checked.

## Test Helpers

`tests/support/` holds helpers for testing ordering, failure and privacy. Each has its own tests next to it. They exist for the bug-fix waves (bug-fix plan §4.2), whose fixes add the fault-injection, ordering, cancellation, content-sentinel and Hypothesis tests.

| Helper | Module | Use it to |
|--------|--------|-----------|
| `FaultInjector` | `fault_injection.py` | Make the Nth awaited call on a collaborator raise, or raise `asyncio.CancelledError` to simulate a cancellation at that await |
| `RecordingUnitOfWork` | `recording_unit_of_work.py` | See whether a unit of work committed or rolled back: its `hooks` list records `"commit"` and `"rollback"` as the storage hooks run, so tests need not read private state |
| `OrderingSpy` | `ordering.py` | Record the awaited calls of several collaborators in one log and assert their order, for example that the outbox append comes before the lock release |
| `CONTENT_SENTINEL` and `assert_absent_from_*` | `content_sentinel.py` | Prove that a content field never reaches logs, `repr()` or stored error strings |
| Leaked-task check | `leaked_tasks.py` | Fail a test that leaves a pending task behind (autouse; see above) |
| Hypothesis profiles | `hypothesis_profiles.py` | Run property and stateful tests reproducibly in CI |

`tests/support/probes/` keeps the scripts that verified the bug audit. They print what the library does and assert nothing. pytest does not collect them (`collect_ignore_glob` in `tests/conftest.py`), ruff and mypy skip them, and they are deleted once every row they cover has a regression test.

### Hypothesis profiles

`tests/conftest.py` registers two Hypothesis profiles and loads the one named by the `HYPOTHESIS_PROFILE` environment variable. No test uses Hypothesis yet; the profiles are ready for the first property and stateful tests:

| Profile | Settings | Used by |
|---------|----------|---------|
| `dev` | Hypothesis' defaults | Local runs (the default when the variable is unset) |
| `ci` | Derandomized, no example database, no deadline, prints the blob that reproduces a failure | `.github/workflows/test.yml` |

```bash
HYPOTHESIS_PROFILE=ci python -m pytest
```

## Docs Examples

`tests/docs/` runs the Python examples of every Diataxis page marked `<!-- docs-test: runnable -->`, so `make test` fails when a page stops matching the library. A page also fails if it defines a test, a `main()` or a pytest fixture and never runs it. [Add a Runnable Docs Example](../../how-to/testing/add-runnable-docs-examples.md) describes the markers and how the blocks run.

## Mutation Testing

Coverage shows which lines ran; mutation testing shows whether the tests would notice a change to them. `make mutate MODULE=src/pydomain/<path>.py` runs `mutmut` on one module. It prints how many mutants each status has, the score (killed / total) and every mutant not killed (survivors and timeouts). A surviving mutant is a change to the code that no test caught.

After a fix, each surviving mutant in the changed functions is killed by a new test or justified in the PR, for example as an equivalent mutant. No PR may lower the mutation score of a module it touches; the baseline scores are in arc42 [§10.4, Mutation baseline](../../../arch42/10-quality.md#mutation-baseline).

## The Test-First Protocol in the Tree

A change to pydomain starts with tests that fail ([ADR-066](../../../adr/ADR-066-test-first-fixes-with-documentation.md)):

1. **Red.** Commit the new tests, each marked strict xfail with the exception it is expected to fail with, before any production change. CI runs on that commit.
2. **Green.** Make the smallest change that passes, and remove the markers in the same commit.
3. **Refactor**, with every test green.
4. **Harden** with `make mutate` on the changed modules.

The same marker records a known defect that an open issue tracks. `tests/testing/test_contracts_repository.py` marks the one Repository contract case that `FakeRepository` fails today:

```python
@pytest.mark.xfail(
    strict=True,
    raises=pytest.fail.Exception,
    reason=(
        "FAKE-01, fixed by DCE-116/W2-6: get_by_id returns the stored "
        "object, so both copies are one object and no conflict is seen"
    ),
)
@pytest.mark.anyio
async def test_saving_a_stale_copy_raises_concurrency_error(
    self, repository: Any
) -> None:
    await super().test_saving_a_stale_copy_raises_concurrency_error(repository)
```

`strict=True` makes the run fail if the case starts to pass, so the fix has to remove the marker. `raises=` makes it fail for the stated reason only; an import error or a typo still fails the run.

## Organizing Your Own Tests

The same structure works for a project that uses pydomain:

| Test type | Where | I/O? | Uses |
|-----------|-------|------|------|
| Entity, value object, aggregate invariants, specifications | One file per domain concept, in a folder that mirrors your domain package | No | Domain objects only |
| Command, query and event handlers | A folder that mirrors your application package | No | `FakeRepository`, `FakeUnitOfWork` and the other fakes |
| Your adapters (a SQL `Repository`, a broker `MessageSubscriber`) | Next to your infrastructure tests | Yes | A `*Contract` suite from `pydomain.testing.contracts` |
| End-to-end flows | A separate folder, so you can run it on its own | Yes | Real infrastructure |

Put shared fixtures and synthetic domain types in a `conftest.py` next to the tests that use them, and keep each fixture small, so a test reads on its own. Define an `anyio_backend` fixture in your root `conftest.py` and mark async tests with `@pytest.mark.anyio`, as pydomain does:

```python
# tests/conftest.py
import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
```

## Next Steps

- **[Testing Philosophy →](testing-philosophy.md)** — why we test this way
- **[Run a Contract Suite →](../../how-to/testing/run-contract-suites.md)** — test your adapter against its Protocol
- **[Fake Repository how-to →](../../how-to/testing/use-fake-repository.md)** — using fakes in practice
