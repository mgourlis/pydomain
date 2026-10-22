# 10. Quality

This section describes how the library's quality goals (defined in §1.3) are verified and enforced in practice. It covers the quality tree, concrete verification mechanisms, and the testing strategy.

---

## 10.1 Quality Tree

The library's quality attributes are organized into three categories. Each leaf node maps to a verification mechanism described in the subsections below.

```
                         pydomain Quality
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                     │
     Architectural          Correctness          Operational
     Quality                Quality               Safety
          │                    │                     │
    ┌─────┴─────┐       ┌─────┴─────┐        ┌─────┴──────┐
    │           │       │           │        │            │
  Module     Type     Test        Code    Optimistic   Publish-
  Boundaries Safety   Coverage    Quality Concurrency  After-Commit
  (§10.2)    (§10.3)  (§10.4)     (§10.5) (§10.6)      (§10.6)
```

---

## 10.2 Module Boundary Enforcement

The strict layered dependency graph (§2.5) is the library's most important architectural invariant. Violations — a `ddd/` file importing from `cqrs/`, or `cqrs/` importing from `es/` — are considered architecture bugs.

### Enforcement mechanism

| Mechanism | How | When |
|-----------|-----|------|
| **Ruff `TID252`** | Bans relative imports — forces absolute `pydomain.*` paths so cross-module dependencies are explicit and auditable | `make lint` (every commit) |
| **Architecture tests** | `tests/test_architecture.py` asserts the import rules below with `pytest-archon` archrules, checks that `ddd/` imports no third-party package but pydantic and uuid-utils, and checks in a fresh interpreter what importing a layer loads. The last checks are strict xfail until W2-11: `pydomain/__init__.py` loads every layer (DDD-17) | `make test` and CI |
| **Code review** | `architect-reviewer` checks import paths during DDD boundary review (CLAUDE.md workflow step ⑩) | Pre-merge |
| **Manual audit** | `grep` / Pyright workspace scan for forbidden cross-module imports | Periodic; onboarding new modules |

### Permitted imports by module

```
ddd/           → pydantic, uuid, datetime, typing, logging, uuid_utils, stdlib
cqrs/          → ddd/ + same external libs as ddd/;
                 anyio in cqrs/unit_of_work.py only (the shield helper, B-128)
es/            → ddd/ + same external libs as ddd/
infrastructure/→ ddd/, cqrs/, es/
testing/       → ddd/, cqrs/, es/, infrastructure/ (not pytest; not anyio directly)
testing/contracts/ → ddd/, cqrs/, es/, infrastructure/, pytest, anyio
                     (not the fakes; no module imports testing/contracts/)
_describe.py   → pydantic only; every layer may import it (ADR-071)
```

### What is not allowed

| Violation | Example | Why it's wrong |
|-----------|---------|----------------|
| `ddd` → `cqrs` | `from pydomain.cqrs.command import Command` | Domain layer depends on application layer |
| `ddd` → `es` | `from pydomain.es.event_store import EventStore` | Domain layer depends on event-sourcing mechanism |
| `cqrs` → `es` | `from pydomain.es.aggregate import ...` | CQRS layer must work without event sourcing |
| `es` → `cqrs` | `from pydomain.cqrs.command_bus import CommandBus` | ES layer must work without CQRS buses |
| `infrastructure` → `testing` | `from pydomain.testing.fakes import ...` | Production code depends on test doubles |
| any module but `testing/contracts/` → `pytest` | `import pytest` in `pydomain.testing.fake_repository` | Makes the `testing` extra a runtime dependency |
| any module but `testing/contracts/` and `cqrs/unit_of_work.py` → `anyio`, directly | `import anyio` in `pydomain.cqrs.behaviors` | anyio is a runtime dependency for the shield helper alone (ADR-079, B-128); other modules call the helper |
| any module → `testing/contracts/` | `from pydomain.testing.contracts import RepositoryContract` in `pydomain.testing` | Loads pytest whenever pydomain is imported |
| `testing/contracts/` → a fake | `from pydomain.testing import FakeRepository` | The suites test the fakes; they must not depend on them |
| `_describe` → any pydomain module | `from pydomain.cqrs.exceptions import ...` in `pydomain._describe` | Every layer imports it, so it must stay a leaf |

---

## 10.3 Type Safety

Python 3.12 generics (PEP 695) bind types at the point of use, eliminating `Any` returns from public APIs. The type checker verifies this at development time.

### Type checker configuration

Two type checkers are used, both in strict mode:

| Checker | Config | Command |
|---------|--------|---------|
| **Pyright** (Pylance) | `pyrightconfig.json`: `typeCheckingMode = "strict"`, `pythonVersion = "3.12"` | VS Code inline; `pyright` CLI |
| **mypy** | `pyproject.toml`: `disallow_untyped_defs`, `disallow_untyped_calls`, `disallow_incomplete_defs`, `pydantic.mypy` plugin | `make type` |

### Generic type bindings

Every generic base class binds its type parameters at the user's declaration site, making `dispatch()` return the exact declared type:

```
Entity[TId]            → id: TId, version: int
AggregateRoot[TId]     → inherits Entity[TId] + event buffer
Command[TResult]       → dispatch() → TResult
Query[TResult]         → dispatch() → TResult
Repository[T, TId]     → save(T), get_by_id(TId) → T
Saga[S]                → state type is S
IdGenerator[TId]       → generate() → TId
```

### Runtime type safety

Where generics are erased at runtime, explicit type guards fill the gap:

| Guard | Location | What it checks |
|-------|----------|----------------|
| `isinstance(generated, TId)` | `Entity._ensure_id()` | Auto-generated ID matches declared type; raises `DomainError` on mismatch |
| `@runtime_checkable` | All `Protocol` classes | Enables `isinstance()` checks for defensive validation |
| `extra = "forbid"` | `Command[TResult]`, `Query[TResult]` | Rejects unknown fields at construction time |

---

## 10.4 Testing Strategy

Every change that alters library behaviour is test-first and ships with its documentation (ADR-066). The protocol is in `docs/others/bug-fix-plan.md` §4 and in the PR template (`.github/PULL_REQUEST_TEMPLATE.md`). Coverage is measured, but it proves little on its own: every defect the 0.3.0 audit confirmed sat on covered lines. The suite therefore tests contracts (Protocol suites, runnable docs), not only lines. The helpers for failure paths (fault injection, ordering, cancellation, content leaks) and the Hypothesis profiles serve the bug-fix waves (bug-fix plan §4.2); the Wave 1 fixes and their review probes brought the first tests that use them.

### Test infrastructure

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Test runner | `pytest >= 8.0` | Discovery, fixtures, parametrize. `[tool.pytest.ini_options]` sets `testpaths = ["tests"]` and adds coverage to every run |
| Async tests | `anyio >= 4.0` and the pytest plugin it ships | A test opts in with `@pytest.mark.anyio` (or a module-level `pytestmark`). `tests/conftest.py` defines `anyio_backend`, which returns `"asyncio"`. There is no `asyncio_mode` setting: that option belongs to `pytest-asyncio`, which the project does not use |
| Coverage | `pytest-cov` | Branch coverage of `src/pydomain` (`[tool.coverage.run]`), reported in the terminal and as HTML |
| Architecture rules | `pytest-archon` | `tests/test_architecture.py` enforces the layer rules of §10.2 as tests |
| Test doubles | `pydomain.testing` | A fake or in-memory adapter for every storage and messaging Protocol (table below) |
| Contract suites | `pydomain.testing.contracts` | One reusable `*Contract` suite per Protocol, 12 in all, run against every shipped adapter (ADR-067) |
| Property and stateful tests | `hypothesis >= 6.100` | Profiles `ci` and `dev`, chosen by the `HYPOTHESIS_PROFILE` environment variable. Property tests cover upcaster chains and stored versions, specifications, snapshot restores, strict-JSON integration-event payloads (`tests/cqrs/test_integration_events.py`) and negative read offsets (`tests/es/test_fake_event_store.py`); a stateful test covers the outbox relay |
| Docs examples | `tests/docs/` | Runs the Python blocks of every Diataxis page marked runnable |
| Test helpers | `tests/support/` | Fault injector, ordering spy, content sentinel, leaked-task check, all in use outside `tests/support/` |
| Mutation testing | `mutmut >= 3.8` | `make mutate MODULE=src/pydomain/<path>.py` |

### Coverage target

≥ 90% branch coverage across all modules (defined in §1.7). A change must not lower the branch coverage of a file it touches.

### Test file organization

The folders mirror the packages under `src/pydomain/`, plus folders for the docs, the tooling and the test helpers:

```
tests/
├── conftest.py               # anyio_backend (asyncio), leaked-task check, Hypothesis profile, probes ignored
├── test_architecture.py      # Layer rules of §10.2 (pytest-archon)
├── test_package_exports.py   # __all__ of every public package
├── test_sensitive_payloads.py # Content sentinel across library failure paths and reprs (ADR-071)
├── ddd/                      # pydomain.ddd: entity, value object, aggregate root, domain event,
│                             #   domain-event version, domain service, specification, factory,
│                             #   ID generator, repository, exceptions
├── cqrs/                     # pydomain.cqrs: commands, queries, buses, handlers, pipeline, behaviors,
│   │                         #   unit of work, outbox, locking, integration events, projection
│   └── saga/                 # Mirrors pydomain.cqrs.saga: a smaller unit set (saga, state, manager,
│                             #   registry) plus test_fake_saga_repository.py
├── saga/                     # Most saga tests: unit suites (saga, state, manager, registry, hydrate,
│                             #   exceptions) plus flows, durability, recovery, interruption, resume,
│                             #   fail, timeouts, pruning, tracing
├── es/                       # pydomain.es: event-sourced aggregate and repository, event store,
│                             #   event stream, snapshots, upcasting, projection, subscription runner
├── infrastructure/           # pydomain.infrastructure: bootstrap, MessageBus, event registry,
│                             #   message broker and subscriber, inbound and outbound gateways
├── testing/                  # pydomain.testing: every *Contract run against its shipped adapter
│                             #   (test_contracts_*.py), the suites' own behaviour, fake-specific tests
├── docs/                     # Docs example runner (doc_examples.py) and its tests
├── tooling/                  # The commit-msg hook (.github/scripts/check-commit-msg.sh)
└── support/                  # Test helpers and their tests
    └── probes/               # Audit and review probes whose rows are still open; not collected
```

Four folders have a `conftest.py` with the synthetic types and fixtures their tests share:

- `cqrs/`: sample commands, results and handlers, and fixtures for a `CommandBus`, a `QueryBus` and a `FakeUnitOfWork`.
- `es/`: order events and an event-sourced order aggregate.
- `saga/`: order-fulfilment events and commands, sample sagas, and fixtures for a `FakeSagaRepository`, registries and managers.
- `infrastructure/`: an order-placed event, and fixtures for a `MessageBus` and a `FakeUnitOfWork`.

New saga tests go in `tests/saga/`, next to the existing suite for the class they test.

### Test helpers (`tests/support/`)

| Helper | Module | What it does |
|--------|--------|--------------|
| **Fault injector** | `fault_injection.py` | `FaultInjector(target, at=N, raises=…)` wraps an object; its Nth awaited call raises the exception, or `asyncio.CancelledError` to simulate a cancellation at that await. `after=True` lets the call take effect first |
| **Recording unit of work** | `recording_unit_of_work.py` | `RecordingUnitOfWork` is a `FakeUnitOfWork` whose `hooks` list records `"commit"` and `"rollback"` as its storage hooks run, so a test sees whether a scope committed or rolled back without reading private state |
| **Ordering spy** | `ordering.py` | `OrderingSpy` wraps several collaborators and records their awaited calls in one shared log; `assert_order(...)` checks the order of side effects across them |
| **Content sentinel** | `content_sentinel.py` | `CONTENT_SENTINEL` goes into a content field; `assert_absent_from_logs`, `assert_absent_from_reprs` and `assert_absent_from_strings` prove it never reaches logs, `repr()` or stored error strings |
| **Leaked-task check** | `leaked_tasks.py` | Autouse fixture `check_leaked_tasks`: fails an anyio test on asyncio that leaves a pending task behind. Opt out with `@pytest.mark.allow_leaked_tasks(reason="DCE-NN …")`. Sync tests that drive their own loop are not checked |
| **Hypothesis profiles** | `hypothesis_profiles.py` | `ci` is reproducible: derandomized, no example database, no deadline, prints the reproduction blob. `dev` keeps Hypothesis' defaults. `tests/conftest.py` loads the one named by `HYPOTHESIS_PROFILE` (`dev` when unset); `.github/workflows/test.yml` sets `ci` |
| **Probes** | `probes/` | The scripts that verified the bug audit, and the Wave 1 review probes. They print observations and do not assert. Not collected (`collect_ignore_glob` in `tests/conftest.py`) and excluded from ruff and mypy. DCE-127 deleted a script once every scenario it covers had a committed test, or was skipped for a stated reason (a backend that is not installed, timing only, the CI dependency-floor job, a probe of behaviour that no longer exists). The scripts left reproduce rows that are still open (Wave 2 and 3 clusters, and the saga rows left to W2-5) or belong to the docs-runner guard (DCE-126), and stay until those rows are fixed (decision-log P-28); `probes/README.md` names each script's owner |

Each helper has its own tests next to it (`tests/support/test_*.py`). They serve the bug-fix waves (bug-fix plan §4.2), whose fixes use them for the fault-injection, ordering, cancellation, content-sentinel and Hypothesis tests. The content sentinel is used by `tests/test_sensitive_payloads.py`, which checks every library failure path and `repr()` that ADR-071 covers.

### Contract suites

`pydomain.testing.contracts` ships one pytest base class per Protocol (ADR-067). Each case asserts only what the Protocol's docstring or its ADR promises. pydomain runs every suite against its own fake or in-memory adapter in `tests/testing/test_contracts_*.py`, and `RepositoryContract` also against `EventSourcedRepository` over `FakeEventStore`. Where a shipped adapter breaks a promise today, its run marks that case `xfail(strict=True)` with the issue that tracks it. Adopters run the same suites against their own adapters (`docs/diataxis/how-to/testing/run-contract-suites.md`).

### Docs examples

`tests/docs/test_diataxis_examples.py` runs every Diataxis page marked `<!-- docs-test: runnable -->`: its `python` blocks run top to bottom in one namespace, with top-level `await`. `<!-- docs-test: skip -->` leaves out the next block, `<!-- docs-test: setup … -->` adds hidden setup code, and `<!-- docs-test: check … -->` adds hidden code in page order, such as calls to the tests a block defines. A page fails if it never runs a `test_*` function, `main()`, `test_*` method of a `Test*` class or pytest fixture it defines; `run_tests()` runs tests with their fixtures from a check block. After each page the runner removes the `sys.modules` names and logging handlers the page added, even when a block failed. A page that taught a defect is fixed in the same PR as the fix and marked runnable, so `make test` fails when it drifts from the library. The convention is in `docs/diataxis/how-to/testing/add-runnable-docs-examples.md`.

### Mutation testing

`make mutate MODULE=src/pydomain/<path>.py` runs `mutmut` on one module. mutmut generates mutants for all of `src/pydomain` (`[tool.mutmut]` `source_paths`) but runs only that module's. The target prints the count of mutants per status, the score (killed / total), and every mutant not killed (survivors and timeouts). mutmut copies `docs/` and `.github/` into its working copy, because the docs example and commit-msg hook tests read them. A Protocol whose methods have default arguments carries `# pragma: no mutate block`, so runtime `isinstance` checks against it keep working.

The rule: after a fix, run `make mutate` on each module it changed. Every surviving mutant in the changed functions is killed by a new test or justified in the PR (an equivalent mutant, or dead code the PR removes). For that comparison a mutant that times out counts as killed (the suite hangs on it, so it is detected), an equivalent argument is removed rather than left, and unreachable code carries `# pragma: no mutate`: "no PR may lower the score" reads as "no unexplained survivors" (P-41). The scores to compare against, and the rule that no PR may lower them, are in [Mutation baseline](#mutation-baseline).

### Fakes over mocks

The library follows the principle: **never mock what you don't own**. Test doubles are complete in-memory implementations of `Protocol` interfaces, not `unittest.mock.MagicMock` patches. They assume one event loop on one thread (§2.1). All are exported from `pydomain.testing`.

| Fake | Module | Satisfies | Key behaviour |
|------|--------|-----------|---------------|
| `FakeRepository[T, TId]` | `fake_repository.py` | `Repository[T, TId]` | Dict keyed by aggregate ID; version check on update raises `ConcurrencyError`; `save()` drains pending events for `pull_events()` |
| `FakeUnitOfWork` | `fake_unit_of_work.py` | `UnitOfWork` (subclasses `AbstractUnitOfWork`) | Takes one `repository` or a dict of `repositories`; `_flush()` and `_commit()` do nothing; the read-only `rolled_back` property (over the private `_rolled_back`) records the current scope's rollback and is reset on entry; a rollback drains the repositories' event buffers but keeps their saves; `fail_at` (`"flush"`, `"write_outbox"`, `"commit"`) makes that hook raise `failure` (default: a new `RuntimeError`) once, in the next scope that reaches it, then resets to `None` (B-127) |
| `FakeEventStore` | `fake_event_store.py` | `EventStore` | Streams per aggregate plus a global log; `expected_version` check; `DuplicateCommandError` on a repeated `command_id`, which is recorded only when its append succeeds; an empty append runs both checks but stores nothing, creates no stream and records no `command_id`; `ValueError` for a negative `from_version` (ADR-072) |
| `FakeSnapshotStore` | `fake_snapshot_store.py` | `SnapshotStore` | Dict keyed by aggregate type and ID; no serialization; stores and returns deep copies (ADR-075) |
| `FakeCheckpointStore` | `fake_checkpoint_store.py` | `CheckpointStore` | Dict keyed by subscription ID; `0` for an unknown subscription |
| `FakeOutboxStore` | `fake_outbox_store.py` | `OutboxStore` (structurally) | Records appends, publish marks, failures and dead letters; a fetch claims its entries until they are marked or `claim_timeout` (default five minutes) passes, and `mark_failed` releases the claim; `ValueError` for a `limit` below `1`; injectable clock; `fail_next_fetch` and `fail_next_append` simulate outages (ADR-074) |
| `FakeProcessedMessageStore` | `fake_processed_message_store.py` | `ProcessedMessageStore` | Dict keyed by message ID; `get`, `set`, `contains`, `check_and_set` |
| `FakeLockProvider` | `fake_lock_provider.py` | `LockProvider` | One `asyncio.Lock` per key, created on first `acquire()`; not reentrant: the holding task's second `acquire()` raises `LockReentryError`; each acquire keeps a hold token in the calling context; a release with a stale token does nothing, one from a context without a token (including a second release, since a release drops its token) raises `RuntimeError` (ADR-076) |
| `FakeSagaRepository` | `fake_saga_repository.py` | `SagaRepository` | Dict keyed by saga ID; deep copies on save and read; correlation, stalled, suspended and expired queries |
| `InMemoryMessageBroker` | `in_memory_message_broker.py` | `MessageBroker` (structurally) | Captures published events in a list; `start()` and `stop()` do nothing |
| `InMemoryMessageSubscriber` | `in_memory_message_subscriber.py` | `MessageSubscriber` (structurally) | Records subscriptions, one handler per topic; `simulate_message()` delivers a message as if from a broker and raises `KeyError` for a topic with no handler (ADR-072) |
| `InMemoryProjectionStore` | `in_memory_projection_store.py` | `ProjectionStore` | Dict keyed by projection ID |

Each fake has a matching `*Contract` suite run against it in `tests/testing/`. A fake keeps every promise of its Protocol and of its own docstrings; a fix that restores one adds the Protocol's promise to the suite (ADR-072).

### Test-first protocol

Every change that alters behaviour follows red, green, refactor, harden (ADR-066; bug-fix plan §4.1). The commits show each step, although the PR is squash-merged.

| Step | What | Commit |
|------|------|--------|
| **0. Specify** | Write the acceptance criteria in the issue as test names, each naming the contract it checks (ADR, docstring or doc page) | — |
| **1. Red** | Write the tests, each marked `@pytest.mark.xfail(strict=True, raises=<expected failure>, reason="DCE-NN …")`. Run them locally to see each fail for its stated reason, and do not push this commit on its own (P-32): it goes up together with the green commit. Strict mode turns an unexpected pass into a failure; `raises=` makes the test fail for the intended reason, never an import error or a typo | `DCE-NN: test(<scope>): …` |
| **2. Green** | The smallest change that makes the tests pass; remove the markers | `DCE-NN: fix(<scope>): …` (or `feat`) |
| **3. Refactor** | Only with every test green; no behaviour change | `DCE-NN: refactor(<scope>): …` (optional) |
| **4. Harden** | `make mutate` on the changed modules; kill or justify every surviving mutant in the changed functions | `DCE-NN: test(<scope>): …` |

Rules for the tests:

- **Assert the contract, not the implementation.** Observe through the public API, spies or the contract suites, never private attributes.
- **Never weaken a test to make a change pass.** A test that blessed a defect changes in the same PR, and the PR says why.
- **One behaviour per test**, named after it.
- **Per-test fixtures.** No shared unit-of-work instance unless the test is about sharing.
- **Deterministic.** Concurrency tests force the interleaving with explicit yields or events, never with sleeps. Hypothesis runs in CI use the `ci` profile.
- **Synthetic data only** (orders, invoices, shipments).

A change includes every test layer that applies: regression example, property test, stateful test (`RuleBasedStateMachine`), contract-suite case, failure and cancellation injection, content sentinel, typing test, docs example test (bug-fix plan §4.2).

### Test definition of done

From the PR template's "Tests (test-first)" checklist:

- [ ] Acceptance criteria are in the issue as test names.
- [ ] A red commit with `xfail(strict=True, raises=...)` precedes the fix commit, its tests fail locally for their stated reasons, and the PR summarises that run (P-32).
- [ ] Every applicable layer is present.
- [ ] No test asserts private state; no existing test was weakened (changed tests say why).
- [ ] Mutation testing on the changed functions; survivors killed or justified.
- [ ] Branch coverage of changed files did not fall; `make check` and `make test` pass.

CI (`.github/workflows/test.yml`) runs ruff lint, the ruff format check, mypy and pytest with `HYPOTHESIS_PROFILE=ci` on Python 3.12, 3.13 and 3.14. A `floor` job installs the runtime dependencies at the floors `pyproject.toml` declares (`>=` read as `==`) and runs pytest on Python 3.12 and 3.14 (B-75, B-77); since ADR-079 that includes `anyio==4.0`, so the shield tests run at the declared anyio floor (B-133).

### Test categories

| Category | Scope | I/O | Where |
|----------|-------|-----|-------|
| **Domain unit tests** | Single class (Entity, VO, Aggregate, Specification) | None | `tests/ddd/` |
| **Bus and handler tests** | CommandBus, QueryBus, pipeline behaviors, handlers with `FakeUnitOfWork` and `FakeRepository` | None (fakes) | `tests/cqrs/`, `tests/infrastructure/` |
| **Saga tests** | Saga orchestration, hydration, compensation, recovery | None (fakes) | `tests/saga/` (most), `tests/cqrs/saga/` |
| **ES tests** | `EventSourcedRepository` with `FakeEventStore` and `FakeSnapshotStore`, upcasting, subscriptions | None (fakes) | `tests/es/` |
| **Contract-suite runs** | Every `*Contract` against its shipped adapter | None (fakes) | `tests/testing/` |
| **Architecture and exports** | Layer rules, public `__all__`, no test framework loaded by `import pydomain` | None | `tests/test_architecture.py`, `tests/test_package_exports.py`, `tests/testing/test_contracts_packaging.py` |
| **Docs examples** | The Python blocks of every Diataxis page marked runnable | None | `tests/docs/` |
| **Tooling** | The commit-msg hook | Runs `bash` | `tests/tooling/` |

All tests run without infrastructure. No Docker, no database, no message broker.

### Mutation baseline

These scores are a baseline, not a target ([audit plan §4.8](../others/bug-audit-plan.md#48-mutation-testing)). No PR may lower the score of a module it touches ([fix plan §4.4](../others/bug-fix-plan.md#44-mutation-testing)).

Reproduce one score with `make mutate MODULE=src/pydomain/<path>.py`. Measured on 2026-10-21 at commit `b0214eb` with mutmut 3.8.0 on Python 3.12.3. Score is killed / mutants; a timeout is not counted as killed here, though a PR counts it as killed (P-41, [Mutation testing](#mutation-testing)).

Some code is outside these scores. mutmut 3.8 does not mutate a function with a decorator other than a single `@classmethod` or `@staticmethod`, so Pydantic validators and serializers and `@property` methods are skipped. Protocols marked `# pragma: no mutate block` are skipped too (of the modules above, only `OutboxStore` in `cqrs/outbox.py`). In particular, the `ddd/entity.py` score covers equality, hashing and `configure`, but not the id guard (`Entity._ensure_id`) that audit plan §4.8 names.

| Module | Mutants | Killed | Survived | Timeout | Score |
|--------|--------:|-------:|---------:|--------:|------:|
| `ddd/entity.py` | 9 | 8 | 1 | 0 | 89% |
| `ddd/specification.py` | 15 | 14 | 1 | 0 | 93% |
| `cqrs/behaviors.py` | 187 | 154 | 33 | 0 | 82% |
| `cqrs/unit_of_work.py` | 24 | 22 | 2 | 0 | 92% |
| `cqrs/command_bus.py` | 65 | 49 | 16 | 0 | 75% |
| `cqrs/outbox.py` | 62 | 54 | 8 | 0 | 87% |
| `cqrs/saga/saga.py` | 243 | 192 | 51 | 0 | 79% |
| `cqrs/saga/state.py` | 76 | 64 | 12 | 0 | 84% |
| `es/event_sourced_repository.py` | 87 | 77 | 10 | 0 | 89% |
| `es/snapshot.py` | 15 | 13 | 2 | 0 | 87% |
| `es/upcasting.py` | 37 | 33 | 3 | 1 | 89% |
| `infrastructure/message_broker.py` | 144 | 95 | 41 | 8 | 66% |
| `infrastructure/subscription.py` | 77 | 54 | 20 | 3 | 70% |
| **Total** | **1041** | **829** | **200** | **12** | **80%** |

`make mutate` prints the module's mutants that were not killed (survivors and timeouts) after its score. `python -m mutmut results | grep -v 'not checked'` lists every mutant not killed in the runs so far, and `python -m mutmut show <mutant name>` shows one mutant's diff, using the name as `results` prints it.

---

## 10.5 Code Quality Toolchain

The library enforces code quality through automated tooling integrated into the development workflow.

### Linting and formatting

| Tool | Configuration | Enforcement |
|------|--------------|-------------|
| **Ruff** (linter) | `target-version = "py312"`, `line-length = 88` | Rules: `E` (errors), `F` (pyflakes), `I` (isort), `UP` (pyupgrade), `TID` (tidy imports, including `TID252`). `make lint`. |
| **Ruff** (formatter) | Same config as linter | `make format`. Replaces Black. |

### Static analysis

| Tool | Configuration | Purpose |
|------|--------------|---------|
| **Pyright** | `typeCheckingMode = "strict"` | Primary type checker (VS Code Pylance) |
| **mypy** | The individual strict flags in `[tool.mypy]` (`disallow_untyped_defs`, `disallow_untyped_calls`, `disallow_incomplete_defs`, `check_untyped_defs`, `warn_return_any` and others; there is no `strict = true` key), `pydantic.mypy` plugin enabled | Secondary type checker (`make type`) |

### Make targets

```bash
make help                                   # List the targets
make install                                # pip install -e ".[dev]"
make pre-commit                             # Install the pre-commit hooks
make lint                                   # Ruff lint check
make format                                 # Ruff auto-format
make type                                   # mypy type checking
make test                                   # pytest with coverage
make check                                  # lint + type
make mutate MODULE=src/pydomain/<path>.py   # mutmut on one module (§10.4)
make clean                                  # Remove caches, coverage, mutants/ and .venv/
```

`make check` and `make test` together are the local quality gate. CI also checks the formatting (`ruff format --check`). All must pass before merge.

---

## 10.6 Operational Safety Mechanisms

These mechanisms protect production correctness at runtime. They are design-level guarantees, verified by tests.

### Optimistic concurrency

Every `AggregateRoot` carries a `version: int` field. The repository checks this on `save()` — if the persisted version differs from the in-memory version, a `ConcurrencyError` is raised. This prevents lost updates when two handlers mutate the same aggregate concurrently.

```
Handler A loads Order (version=3)
Handler B loads Order (version=3)
Handler B saves Order (version=3→4)  ← succeeds
Handler A saves Order (version=3→4)  ← ConcurrencyError (expected 3, found 4)
```

### Publish-after-commit

Domain events are only dispatched to handlers *after* the Unit of Work commits successfully (§8.3). This guarantees event handlers never see uncommitted state.

### Rollback discards staged events, once per failure

A failed command's events never reach a later commit. `rollback()` drains the event buffer of every repository the unit of work holds, so a repository shared across units of work cannot leak them; the scope rolls back whenever it leaves without committing, exactly once per failed dispatch; and a rollback that raises still leaves the caller a `CommandExecutionError` with the original failure as its cause. A second `commit()` in one scope raises `UnitOfWorkAlreadyCommittedError` before anything is flushed or written to the outbox (ADR-068). `UnitOfWorkContract` checks the event-level promises against every adapter (§10.4).

### The commit and the rollback finish under cancellation

`AbstractUnitOfWork` runs `_commit()` and `_rollback()` to completion when the calling task is cancelled while they run, and re-raises a native cancellation afterwards; an anyio scope's cancellation arrives at the next checkpoint instead, so a dispatch with none left returns normally, with its events (ADR-079 point 6, CQRS-07). A commit whose storage write took effect is therefore never rolled back by a late cancellation: the scope is marked committed, the exit drains, and `IdempotencyBehavior` records the result, so a retry is not run again (B-109, B-121). A second cancellation no longer cuts a rollback short. The shield is `anyio.CancelScope(shield=True)` plus, under asyncio, a hook task in the caller's context that the caller waits on through a private future: no busy loop under an anyio cancel scope, no eager start under `asyncio.eager_task_factory`, and nothing logged by the loop for a hook's failure (ADR-071). `_flush()` and `_write_outbox()` stay cancellable: a cancellation there rolls back with nothing lost (B-110). A unit of work whose storage is keyed by the current task sets `shield_hooks = False` and gives up the shield (B-115, B-119); a Protocol-only unit of work shields its own hooks. A cancelled dispatch can outlive its cancellation by the length of the commit, the rollback or the post-commit record. `tests/cqrs/test_unit_of_work_shield.py` cancels at each await of `commit()`, natively, repeatedly, through `asyncio.timeout`, a `TaskGroup` and an anyio scope, on Python 3.12 to 3.14 and at the anyio floor. `UnitOfWorkContract` has no case for it: the promise does not hold for a conforming adapter that sets `shield_hooks = False` or implements only the Protocol.

### Payload values stay out of library text

Errors the library stores (outbox `mark_failed` and `mark_dead_lettered`, `SagaState.error`, `suspension_reason`, `failed_compensations`), its log lines on failure paths and the `UpcastError` it builds carry type names, field locations and Pydantic error codes, never payload values, except the handler-failure logs left to W2-3 (listed in §8.13); payload fields are left out of `repr()` (§8.13, ADR-071). `tests/test_sensitive_payloads.py` puts the content sentinel (§10.4) into a payload on every one of these paths and checks that it is absent from the stored strings, the captured log records (including formatted tracebacks) and the `repr()`; the exact descriptions are tested next to each module, and `tests/test_describe.py` tests the helper.

### Outbox delivery duplicates at most the entry in flight

The outbound relay (§6.6) marks each entry published right after the broker accepts it, so a store failure or a cancellation later in a pass can make the next pass redeliver only the entry that was in flight, never the rest of its batch. A store error ends the pass; a delivery failure does not. A fetch claims what it returns, so two relays do not deliver the same row, and a crashed relay's claims go stale and are fetched again; a publish that outlasts its claim may be delivered twice, and a late failure then never undoes the publication. A stored payload that cannot be validated, whatever it raises, spends its retry budget instead of ending every pass, and the strict-JSON walk is iterative and depth-limited, so no payload raises `RecursionError`. The relay refuses arguments it cannot honour (`batch_size` or `max_attempts` below `1`, negative delays, `max_delay < base_delay`), the registry refuses a second integration class on a topic and a translator that returns the wrong class, and delivered events carry the tracing ids of the domain event that caused them (ADR-074). `tests/infrastructure/test_outbox_relay_stateful.py` checks the invariants with a Hypothesis `RuleBasedStateMachine` over two relays sharing one store, appends, passes, broker failures, store failures, cancellations, relays that crash after a fetch, publishes that outlast their claim, and clock moves: no entry is both published and dead-lettered, the store marks published only what the broker accepted, attempts never exceed `max_attempts` plus one for each relay that redelivered the entry through a stale claim (B-80), an entry is redelivered only after its own mark was lost, backoff never shrinks and is capped, and every entry ends published or dead-lettered. `OutboxStoreContract` pins the claim and the `limit` rule for every adapter (§10.4). Four lifecycle gaps remain for a later wave: a hung `publish()` blocks `stop()`, a dead relay task restarts only after `stop()`, a `stop()` before a directly created `run()` task begins is lost, and after `start()` a direct `run()` task makes `stop()` wait forever.

### Locks are released however a command ends

`AggregateLockingBehavior` releases every key whose `acquire()` returned, in reverse order, after the handler and the commit return (the commit runs inside `next()`, so the keys are held through it, ADR-079) or after any `BaseException` from an `acquire()`, the handler or the commit, a cancellation included. The releases run inline in the acquiring task; when a cancellation arrives during them (a second `cancel()`, an `asyncio.timeout`, an anyio cancel scope), the interrupted key and the rest move to one background task and the cancellation propagates, also when the provider converts or swallows it, so the cancellation does not stop the releases. The interrupted key is retried there, which is safe only because `release()` is ownership-safe: a late release never frees the next holder's lock (`LockProviderContract`, B-68). Cancellation handling is asyncio-only; under trio the releases run inline (B-69). One failing `release()` does not skip the others. A failed release after a successful commit is a type-only WARNING on `pydomain.pipeline` and the result is returned; after a failed handler it is a `clean-up also failed: <type>` note on the handler's error; a release's own `BaseException` is re-raised after the other releases (ADR-076). Locks are not reentrant: `FakeLockProvider` raises `LockReentryError` on a same-task re-acquire, and `LockProviderContract` checks that no provider grants one (§10.4). `tests/cqrs/test_locking_behavior_release.py` injects the cancellations and failing releases with `FaultInjector`.

### Idempotent command handling

The `IdempotencyBehavior` pipeline behavior tracks `command_id` in a `ProcessedMessageStore`. Duplicate dispatches are detected and return the cached result without re-executing the handler; they commit nothing, and their scope rolls back (B-116). The result is recorded after the commit (ADR-079), so a commit that fails at any of its hooks is never cached and its retry runs the handler again; `tests/cqrs/test_bus.py` injects the failure at `_flush`, `_write_outbox` and `_commit`. A record that fails after the commit is a type-only WARNING and the result is still returned (B-129). A command whose commit completed and whose dispatch then raised (a cancellation during the commit, the record or a lock release, or a handler that committed itself) is recorded too, through the same shield as the commit, before the error propagates; a record that fails there is a `clean-up also failed: <Type>` note on that error (B-121, B-123, B-134). This is at-least-once: a crash between the commit and the record, or an error a `_commit()` raises after the storage commit took effect, runs the handler again on retry (closed in 0.5.0, W3-6). Two concurrent dispatches of one `command_id` can still both run (CQRS-04, phase 4 of ADR-079).

### Saga compensation

The saga subsystem records a compensation stack (LIFO) for each forward step. On failure, compensating actions execute in reverse order, after the forward commands the failing step queued. Saga state tracks `processed_event_ids` to skip duplicate events. Recovery never drops a pending command: one that cannot be hydrated stays pending and suspends its saga, and one saga's failure does not stop the others (ADR-077).

### Schema evolution resilience

- **Upcasting** (`EventUpcaster` chain) transforms old event payloads to current schema at read time — the event log is never modified.
- **Unknown-key stripping** (`hydrate_command()`) removes extra fields before `model_validate()`, so consumers with older schemas don't break when the producer evolves.
- **Weak-schema fallback** (`GenericDomainEvent`) handles unknown event types gracefully instead of raising.

---

## 10.7 Quality Scenarios

Key quality scenarios that tests must cover. These are not exhaustive — they represent the most critical behaviours.

| ID | Quality Attribute | Scenario | Verification |
|----|-------------------|----------|--------------|
| QS-1 | Modularity | Adding a new `Entity` subclass requires importing only `pydomain.ddd` | Import test |
| QS-2 | Modularity | `cqrs` module works when `es` is not imported | No-import test |
| QS-3 | Type safety | `dispatch(Command[TResult])` returns `TResult`, not `Any` | Pyright/mypy verify return type |
| QS-4 | Type safety | Auto-generated ID type mismatch raises `DomainError` | Unit test |
| QS-5 | Correctness | Two entities with same `id` are equal regardless of other fields | Unit test |
| QS-6 | Correctness | `ValueObject` mutations return new instances; original unchanged | Unit test |
| QS-7 | Correctness | `pull_events()` drains the buffer; second call returns empty list | Unit test |
| QS-8 | Correctness | `stamp()` returns a new frozen copy; original event unchanged | Unit test |
| QS-9 | Operational safety | Concurrent aggregate saves raise `ConcurrencyError` | Unit test with fake repo |
| QS-10 | Operational safety | Event handlers see only committed events; a refused command's events never reach the next commit, also through a shared repository | Handler test with UoW; `UnitOfWorkContract`; `tests/cqrs/test_bus.py` `TestFailedDispatch` |
| QS-11 | Operational safety | Duplicate command dispatch returns cached result | Idempotency behavior test |
| QS-12 | Operational safety | Saga compensation stack executes in reverse order | Saga test |
| QS-13 | Testability | All tests run without Docker, database, or message broker | CI runs `make check` with no external services |
| QS-14 | Schema evolution | `hydrate_command()` strips unknown keys before validation | Hydration test |
| QS-15 | Schema evolution | `UpcasterRegistry` transforms old event to current schema | Upcasting test |
