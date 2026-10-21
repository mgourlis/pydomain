# 2. Constraints

This section documents the technical, organizational, and conventional constraints that shape the library's architecture. Constraints are decisions already made — they are not negotiable within the current version and must be respected by all contributors.

## 2.1 Language and Runtime

| Constraint | Value | Rationale |
|-----------|-------|-----------|
| Minimum Python version | **≥ 3.12** | Required for the new-style `class Generic[T]` syntax (PEP 695) used throughout: `Entity[TId]`, `AggregateRoot[TId]`, `Command[TResult]`, `Query[TResult]`, `Repository[T, TId]`, `Saga[S]`. No `TypeVar` boilerplate. |
| Async model | **`async`/`await`** everywhere | All public I/O-facing APIs are `async def`. No synchronous dual API. Consumers must use an async runtime (`asyncio` or `anyio`). |
| Concurrency model | **Single-threaded `asyncio`** assumed | In-memory fakes (`FakeRepository`, `FakeEventStore`, etc.) are not thread-safe. They rely on the single-threaded event loop for correctness. Multi-threaded or multi-process usage requires external synchronization. |

## 2.2 Dependencies

### Runtime Dependencies

The library has exactly **two** runtime dependencies — an explicit design choice to minimize supply-chain surface area and transitive dependency trees.

| Package | Version | Purpose | Where Used |
|---------|---------|---------|------------|
| `pydantic` | ≥ 2.12.5 | Validation, serialization, `BaseModel` as universal base for all domain concepts | Every module: entities, value objects, events, commands, queries, projections, saga state |
| `uuid-utils` | ≥ 0.9 | UUIDv7 generation (`uuid7()`) for time-ordered, sortable identifiers | `id_generator.py` — the default `Uuid7Generator` |

### No Pydantic v1 Support

The library uses **Pydantic v2 APIs exclusively**. There are no v1 compatibility shims, no `@validator` decorators, no `__fields__` access, and no `schema()` calls. Users on Pydantic v1 must migrate before adopting `pydomain`.

| Need | v2 API (used) |
|------|---------------|
| Immutable model | `ConfigDict(frozen=True)` |
| Mutable model | `ConfigDict(frozen=False)` |
| Private field | `PrivateAttr(default_factory=...)` |
| Copy with changes | `model_copy(update={...})` |
| Serialize to dict | `model_dump()` |
| Deserialize from dict | `model_validate(data)` |
| Custom validators | `@field_validator` / `@model_validator` |

### Dev Dependencies

Development-only dependencies are not shipped to library consumers:

| Package | Purpose |
|---------|---------|
| `pytest` ≥ 8.0 | Test runner |
| `pytest-cov` ≥ 5.0 | Coverage reporting |
| `pytest-anyio` | Empty placeholder package; the pytest plugin ships with `anyio` |
| `pytest-archon` | Architecture rules as tests (`tests/test_architecture.py`) |
| `anyio` ≥ 4.0 | Async test execution and its pytest plugin |
| `ruff` ≥ 0.4 | Linting and formatting |
| `mypy` ≥ 1.8 | Static type checking |
| `pre-commit` ≥ 4.0 | Git hook management |
| `hypothesis` ≥ 6.100 | Property-based and stateful tests |
| `mutmut` ≥ 3.8 | Mutation testing |

### Optional Extras

| Extra | Packages | Purpose |
|-------|----------|---------|
| `testing` | `pytest` ≥ 8.0, `anyio` ≥ 4.0 | Only for `pydomain.testing.contracts`, the Protocol contract suites (ADR-067). No other module imports them, so they are never runtime dependencies. |

## 2.3 Build and Packaging

| Constraint | Value | Notes |
|-----------|-------|-------|
| Build backend | **`hatchling`** | Modern PEP 517/518 build backend. No `setup.py`, no `setup.cfg`. All configuration in `pyproject.toml`. |
| Package name | **`pydomain-lib`** | Single package on PyPI, imported as `pydomain`. All five modules ship together — no per-module packages. |
| Install command | `pip install pydomain-lib` | No extras for core functionality. `pip install "pydomain-lib[dev]"` for contributor tooling. `pip install "pydomain-lib[testing]"` for the contract suites in `pydomain.testing.contracts`. |
| Type checking | **`py.typed`** marker (PEP 561) | Consumers get inline type hints from the installed package without a separate stub package. |
| Python package manager | **`pip`** with `venv` (standard library) | Contributors activate `.venv` and run `make install` (`python -m pip install -e ".[dev]"`). No third-party package manager is required — the published package is manager-agnostic. |

## 2.4 Code Quality Toolchain

| Tool | Configuration | Enforcement |
|------|--------------|-------------|
| **Ruff** (linter + formatter) | `target-version = "py312"`, `line-length = 88`, rules: `E`, `F`, `I`, `UP`, `TID` | `make lint` / `make format`. Pre-commit hook. |
| **mypy** (type checker) | `python_version = "3.12"`, strict mode: `disallow_untyped_defs`, `disallow_untyped_calls`, `disallow_incomplete_defs` | `make type`. Pydantic plugin enabled (`pydantic.mypy`). |
| **Pyright** (alternate type checker) | `pythonVersion = "3.12"`, `typeCheckingMode = "strict"` | `pyrightconfig.json` at project root. Used by VS Code Pylance. |
| **pytest** (test runner) | `testpaths = ["tests"]`; branch coverage enabled, source: `src/pydomain`, excludes: `__init__.py`, `tests/`. Layer rules with `pytest-archon` (`tests/test_architecture.py`) | `make test`. Coverage reported in terminal + HTML. |
| **Hypothesis** (property and stateful tests) | Profiles `ci` (derandomized, no example database, no deadline) and `dev` (defaults), registered in `tests/support/hypothesis_profiles.py` | `HYPOTHESIS_PROFILE` selects the profile, `dev` when unset; CI sets `ci`. No test uses Hypothesis yet: the profiles exist for the bug-fix waves (bug-fix plan §4.2), which add the first property and stateful tests. |
| **mutmut** (mutation testing) | `[tool.mutmut]`: `source_paths = ["src/pydomain"]`, `also_copy = ["docs", ".github"]`, pytest run without coverage | `make mutate MODULE=src/pydomain/<path>.py`. No PR may lower the mutation score of a module it touches (§10.4, Mutation baseline). |
| **Docs example runner** | `tests/docs/doc_examples.py`; a Diataxis page opts in with `<!-- docs-test: runnable -->` | `tests/docs/test_diataxis_examples.py`, part of `make test`. |

## 2.5 Module Dependency Constraints

The five modules follow a strict layered dependency graph. Violations are caught at review time and are considered architecture bugs.

```
                    ┌─────────────┐
                    │  testing    │   (uses all modules)
                    └──────┬──────┘
                           │
                    ┌──────┴──────┐
                    │infrastructure│  (uses cqrs + es + ddd)
                    └──┬──────┬───┘
                       │      │
            ┌──────────┘      └──────────┐
            ▼                            ▼
     ┌──────────┐                 ┌──────────┐
     │   cqrs   │                 │    es    │
     └─────┬────┘                 └─────┬────┘
           │                            │
           └──────────┬─────────────────┘
                      ▼
               ┌──────────┐
               │   ddd    │   (no module imports)
               └──────────┘
```

**Rules enforced:**

| Module | May import | May NOT import |
|--------|-----------|----------------|
| `ddd/` | `pydantic`, `uuid`, `datetime`, `typing`, stdlib, `uuid_utils` | `cqrs`, `es`, `infrastructure`, `testing` |
| `cqrs/` | `ddd` + same as `ddd` | `es`, `infrastructure`, `testing` |
| `es/` | `ddd` + same as `ddd` | `cqrs`, `infrastructure`, `testing` |
| `infrastructure/` | `ddd`, `cqrs`, `es` | `testing` |
| `testing/` | All modules | `testing/contracts/`, `pytest`, `anyio` |
| `testing/contracts/` | All modules except the fakes, plus `pytest` and `anyio` | `testing.fake_*`, `testing.in_memory_*`. No module imports `testing/contracts/`. |
| `_describe.py` (private, any layer may import it) | `pydantic` | Any `pydomain` module |

The `ddd/` module is the pure domain core — it has zero knowledge of CQRS, event sourcing, or any infrastructure concern. This boundary is the most critical constraint in the codebase.

## 2.6 API Stability Constraints

| Constraint | Detail |
|-----------|--------|
| Pydantic v2 only | No v1 compatibility layer will be added. If Pydantic v3 introduces breaking changes, the library will bump its major version. |
| `Protocol` over ABC for interfaces | Infrastructure contracts (`Repository`, `EventStore`, `SnapshotStore`, `MessageBroker`, `LockProvider`, `ProcessedCommandStore`) are `typing.Protocol` — structural subtyping. Users never inherit from library ABCs for infrastructure. This is stable and will not change. |
| Generic type parameters are part of the public API | `Entity[TId]`, `AggregateRoot[TId]`, `Command[TResult]`, `Query[TResult]`, `Repository[T, TId]`, `Saga[S]` — these type bindings are contractual. Changing them is a breaking change requiring a major version bump. |
| Library-built text carries no payload values | Errors the library stores, the lines it logs on failure paths (except the handler-failure logs left to W2-3, listed in §8.13), the exception messages it builds and the `repr()` of its payload fields never contain payload values; they name types, field locations and Pydantic error codes (§8.13, ADR-071). A change that formats an exception or a payload into library text must use `pydomain._describe` and keep `tests/test_sensitive_payloads.py` passing. |
| Events are immutable and append-only | Once a domain event type is published, its schema may only evolve additively (new optional fields). Breaking schema changes require upcasters and a major version bump. |

## 2.7 Async Testing Constraints

| Constraint | Detail |
|-----------|--------|
| Test framework | **pytest** with the pytest plugin that ships with `anyio` (the `pytest-anyio` package in the `dev` extra is an empty placeholder) |
| Async tests | Opt-in: each async test is marked `@pytest.mark.anyio`, or its module sets `pytestmark = pytest.mark.anyio`. `tests/conftest.py` defines `anyio_backend`, which returns `"asyncio"`, so pydomain's own tests run on asyncio only. There is no `asyncio_mode` setting; that option belongs to `pytest-asyncio`, which the project does not use. |
| Contract suites | Each `*Contract` in `pydomain.testing.contracts` pins asyncio with its own session-scoped `anyio_backend` fixture; a subclass overrides it to choose another backend (ADR-067). |
| Leaked tasks | An autouse fixture fails an anyio test on asyncio that leaves a pending task behind; `@pytest.mark.allow_leaked_tasks(reason=...)` opts a test out (`tests/support/leaked_tasks.py`). |
| Fakes over mocks | Tests use `FakeRepository`, `FakeUnitOfWork`, `FakeEventStore`, etc. from `pydomain.testing` — not `unittest.mock`. This is a convention constraint, not a technical one. |
| No I/O in unit tests | Domain tests (`tests/ddd/`) have zero I/O. Other tests use fakes, never real databases or brokers. |

## 2.8 Naming Conventions

| Convention | Example |
|-----------|---------|
| Domain events | Past tense: `OrderPlaced`, `PaymentTaken` |
| Commands | Imperative mood: `PlaceOrder`, `ReserveInventory` |
| Queries | Nominative/descriptive: `GetOrder`, `ListCustomers` |
| Exceptions | Suffixed with `Error`: `ConcurrencyError`, `DomainError` |
| Fake test doubles | Prefixed with `Fake`: `FakeRepository`, `FakeEventStore` |
| Infrastructure protocols | Plain names: `Repository`, `EventStore`, `MessageBroker` (not `IRepository`, not `RepositoryInterface`) |
| No utility files | No `utils.py`, `helpers.py`, `common.py`, or `schemas.py` |

## 2.9 Summary

| Category | Key Constraint |
|----------|---------------|
| Language | Python ≥ 3.12 (PEP 695 generics) |
| Dependencies | 2 runtime: `pydantic ≥ 2.12.5`, `uuid-utils ≥ 0.9` |
| Build | `hatchling`, single package, PEP 561 typed |
| Quality | Ruff + mypy + Pyright (all strict) |
| Architecture | Strict module dependency graph; `ddd/` is pure domain |
| API | Pydantic v2 only; `Protocol` for interfaces; generics are contractual |
| Data protection | Library-built errors, logs and reprs carry no payload values, except the handler-failure logs left to W2-3 (listed in §8.13) (ADR-071) |
| Async | All I/O is `async/await`; single-threaded assumption for fakes |
| Testing | pytest with the `anyio` plugin on asyncio; fakes over mocks; contract suites; test-first (ADR-066); no I/O in domain tests |
