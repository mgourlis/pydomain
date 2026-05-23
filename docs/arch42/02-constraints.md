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
| `pydantic` | ≥ 2.7 | Validation, serialization, `BaseModel` as universal base for all domain concepts | Every module: entities, value objects, events, commands, queries, projections, saga state |
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
| `pytest-anyio` | Async test support (framework-agnostic) |
| `anyio` ≥ 4.0 | Async test execution |
| `ruff` ≥ 0.4 | Linting and formatting |
| `mypy` ≥ 1.8 | Static type checking |
| `pre-commit` ≥ 4.0 | Git hook management |

## 2.3 Build and Packaging

| Constraint | Value | Notes |
|-----------|-------|-------|
| Build backend | **`hatchling`** | Modern PEP 517/518 build backend. No `setup.py`, no `setup.cfg`. All configuration in `pyproject.toml`. |
| Package name | **`pydomain`** | Single package on PyPI. All five modules ship together — no per-module packages. |
| Install command | `pip install pydomain` | No extras for core functionality. `pip install pydomain[dev]` for contributor tooling. |
| Type checking | **`py.typed`** marker (PEP 561) | Consumers get inline type hints from the installed package without a separate stub package. |
| Python package manager | **`uv`** for development | Contributors use `uv sync --extra dev`. This is a developer workflow constraint, not a consumer constraint — the published package is manager-agnostic. |

## 2.4 Code Quality Toolchain

| Tool | Configuration | Enforcement |
|------|--------------|-------------|
| **Ruff** (linter + formatter) | `target-version = "py312"`, `line-length = 88`, rules: `E`, `F`, `I`, `UP` | `make lint` / `make format`. Pre-commit hook. |
| **mypy** (type checker) | `python_version = "3.12"`, strict mode: `disallow_untyped_defs`, `disallow_untyped_calls`, `disallow_incomplete_defs` | `make type`. Pydantic plugin enabled (`pydantic.mypy`). |
| **Pyright** (alternate type checker) | `pythonVersion = "3.12"`, `typeCheckingMode = "strict"` | `pyrightconfig.json` at project root. Used by VS Code Pylance. |
| **pytest** (test runner) | Branch coverage enabled, source: `src/pydomain`, excludes: `__init__.py`, `tests/` | `make test`. Coverage reported in terminal + HTML. |

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
| `testing/` | All modules | — |

The `ddd/` module is the pure domain core — it has zero knowledge of CQRS, event sourcing, or any infrastructure concern. This boundary is the most critical constraint in the codebase.

## 2.6 API Stability Constraints

| Constraint | Detail |
|-----------|--------|
| Pydantic v2 only | No v1 compatibility layer will be added. If Pydantic v3 introduces breaking changes, the library will bump its major version. |
| `Protocol` over ABC for interfaces | Infrastructure contracts (`Repository`, `EventStore`, `SnapshotStore`, `MessageBroker`, `LockProvider`, `ProcessedCommandStore`) are `typing.Protocol` — structural subtyping. Users never inherit from library ABCs for infrastructure. This is stable and will not change. |
| Generic type parameters are part of the public API | `Entity[TId]`, `AggregateRoot[TId]`, `Command[TResult]`, `Query[TResult]`, `Repository[T, TId]`, `Saga[S]` — these type bindings are contractual. Changing them is a breaking change requiring a major version bump. |
| Events are immutable and append-only | Once a domain event type is published, its schema may only evolve additively (new optional fields). Breaking schema changes require upcasters and a major version bump. |

## 2.7 Async Testing Constraints

| Constraint | Detail |
|-----------|--------|
| Test framework | **pytest** with `pytest-anyio` plugin |
| Async mode | `asyncio_mode = "auto"` — test functions marked `async def` are auto-wrapped |
| Fakes over mocks | Tests use `FakeRepository`, `FakeUnitOfWork`, `FakeEventStore`, etc. from `pydomain.testing` — not `unittest.mock`. This is a convention constraint, not a technical one. |
| No I/O in unit tests | Domain logic tests (`tests/test_domain.py`) must have zero I/O. Infrastructure tests may use fakes but not real databases or brokers. |

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
| Dependencies | 2 runtime: `pydantic ≥ 2.7`, `uuid-utils ≥ 0.9` |
| Build | `hatchling`, single package, PEP 561 typed |
| Quality | Ruff + mypy + Pyright (all strict) |
| Architecture | Strict module dependency graph; `ddd/` is pure domain |
| API | Pydantic v2 only; `Protocol` for interfaces; generics are contractual |
| Async | All I/O is `async/await`; single-threaded assumption for fakes |
| Testing | `pytest-anyio`; fakes over mocks; no I/O in domain tests |
