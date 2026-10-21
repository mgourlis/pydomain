# 11. Risks and Technical Debt

This section identifies the technical and organisational risks that could affect the library's quality goals (§1.3), along with current mitigations and any remaining technical debt. Each risk is assessed for likelihood, impact, and current mitigation status.

---

## 11.1 Risk Overview

```
                         pydomain Risks
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                     │
    Schema Evolution      Dependency           Operational
    Risks                 Risks                Risks
          │                    │                     │
    ┌─────┴─────┐       ┌─────┴─────┐        ┌─────┴──────┐
    │           │       │           │        │            │
  Event      Saga     Pydantic    Python   Thread      In-Memory
  Schema     State    Version     Version  Safety      Fakes
  Breakage   Bloat    Breakage    Floor    (§11.5)     (§11.6)
  (§11.2)    (§11.3)  (§11.4)    (§11.4)
```

---

## 11.2 Event Schema Evolution

### Risk

Domain events are immutable once persisted. As the domain model evolves, event schemas change. If an old event can no longer be deserialised by the current code, the entire event stream for an aggregate becomes unrecoverable.

| Dimension | Assessment |
|-----------|------------|
| **Likelihood** | High — every production system evolves its event schemas over time |
| **Impact** | Critical — data loss or production outage if replay fails |
| **Category** | Schema evolution |

### Current Mitigation

The library provides a layered defence:

1. **`EventUpcaster` chain** (§8.8): Old events are transformed to the current schema at read time. The event log is never modified. Upcasters are composed in a chain, so v1 → v2 → v3 migrations are incremental. The chain must end at the class's version, or `deserialize` raises `UpcastError`; the version it reached is written to `event_version`, so a re-serialized event is never upcast twice (ADR-069).

2. **`hydrate_command()` unknown-key stripping** (§9.10): When reconstructing commands from stored data, extra fields from newer schemas are silently removed before `model_validate()`. This makes consumers resilient to additive schema changes without requiring code changes.

3. **`GenericDomainEvent` fallback**: Unknown event types are handled gracefully rather than raising — the system does not crash on events from a newer version of the service.

### Residual Risk

- **Non-additive changes** (renaming a field, changing a field type from `str` to `int`) require an explicit upcaster. The library provides the mechanism but cannot enforce that users write one.
- **Upcaster test coverage** is user responsibility. An incorrect upcaster will not be caught by the library's test suite. A missing one is caught on the first read of an event at the uncovered version (`UpcastError`), which is a production read unless the application's own tests read every stored version.
- **Snapshot schema versioning** (**Mitigated** — ADR-053): The library now provides `SnapshotSchemaPolicy` (read-time validation) and `schema_version` on `Snapshot`. `RejectStaleSnapshotPolicy` detects stale snapshots and forces full event replay. Users must still bump `_snapshot_schema_version` on the aggregate when fields change.

### Technical Debt

| Item | Status | Notes |
|------|--------|-------|
| No automated upcaster validation | Partly addressed (ADR-069) | `register` refuses incomplete, downgrading and duplicate upcasters, and `deserialize` refuses a chain that does not end at the class's version. Whether a chain produces a valid current payload is still checked only when an event is read; a test helper that reads one sample per stored version could check it ahead of time. |

---

## 11.3 Saga State Growth and Compensation Stack

### Risk

Long-lived sagas accumulate step history, processed event records, and compensation records. Without bounds, `SagaState` grows unboundedly, increasing storage size, replay time, and memory consumption.

| Dimension | Assessment |
|-----------|------------|
| **Likelihood** | Medium — depends on saga lifecycle duration and event volume |
| **Impact** | Medium — performance degradation; in extreme cases, memory pressure |
| **Category** | Operational |

### Current Mitigation

1. **`max_processed_events` cap**: Limits the number of tracked processed event IDs. Older entries are evicted when the cap is reached.

2. **`max_step_history` cap**: Limits the number of `StepRecord` entries retained. Older steps are pruned.

3. **`prune_history()` method**: Allows explicit pruning of saga state history. Users can call this from a maintenance endpoint or scheduled task.

4. **Compensation records are bounded by steps**: Each step produces at most one compensation record. The `max_step_history` cap indirectly bounds the compensation stack.

### Residual Risk

- **Pruning trades correctness for size**. Once `processed_event_ids` are pruned, a duplicate event delivery (e.g., from a message broker retry) cannot be detected as a duplicate. The saga handler must be prepared to handle idempotency at the application level.

---

## 11.4 Dependency Version Risks

### Risk

The library depends on two runtime packages: `pydantic >= 2.7` and `uuid-utils >= 0.9`. Major version bumps or breaking changes in these dependencies could break the library or force a major version bump of its own.

| Dimension | Assessment |
|-----------|------------|
| **Likelihood** | Medium — Pydantic v3 is on the horizon; Python 3.13+ may change generics behaviour |
| **Impact** | High — breaking changes in Pydantic affect every module |
| **Category** | Dependency |

### Pydantic v3

Pydantic v3 may introduce breaking API changes (as v2 did with v1). The library uses Pydantic v2 APIs exclusively (§2.2, §9.2):

- `model_config = ConfigDict(...)`
- `model_dump()` / `model_validate()`
- `@field_validator` / `@model_validator`
- `PrivateAttr(default_factory=...)`
- `model_copy(update={...})`
- `model_json_schema()`

If Pydantic v3 breaks any of these, the library would need to:
1. Pin to `pydantic >= 2.7, < 3` until migration is complete.
2. Assess migration effort (likely significant, touching every base class).
3. Release a major version bump.

### Python Version Floor

The library requires Python ≥ 3.12 for PEP 695 generics (`class Foo[T]`). If a future Python version changes generic semantics or introduces deprecations, the library may need adjustments. The Python 3.12 floor also limits adoption for teams stuck on 3.11 or earlier.

### Current Mitigation

- **Pinned minimum versions** in `pyproject.toml` prevent silent breakage from `pip install --upgrade`.
- **Strict type checking** (Pyright + mypy in strict mode) catches API surface changes early.
- **Comprehensive test suite** (about 1,750 tests, 96% branch coverage) acts as a regression safety net.
- **CI matrix** runs the suite, ruff and mypy on Python 3.12, 3.13 and 3.14, so a generics or deprecation change in a newer Python fails CI before a release.

### Residual Risk

- **Transitive dependency conflicts**: Users may depend on another library that pins `pydantic < 2.8` or `pydantic >= 3`. The library cannot control this.
- **`uuid-utils` maintenance**: A niche package with a single maintainer. If development stalls or the package is abandoned, the library would need to switch to an alternative (e.g., `uuid6` or stdlib-only UUIDv7 generation in Python 3.13+).

### Technical Debt

| Item | Status | Notes |
|------|--------|-------|
| Pydantic v3 migration plan | Not started | No action needed until v3 is released. Monitor Pydantic roadmap. |
| `uuid-utils` alternative evaluation | Open | Python 3.13 includes `uuid.uuid8()`; stdlib UUIDv7 may follow. |

---

## 11.5 Thread Safety of In-Memory Fakes

### Risk

All fakes in `pydomain.testing` (`FakeRepository`, `FakeEventStore`, `FakeUnitOfWork`, etc.) use plain Python `dict` and `list` for storage. They are **not thread-safe**. The library assumes single-threaded `asyncio` execution (§2.1).

| Dimension | Assessment |
|-----------|------------|
| **Likelihood** | Low — standard `asyncio` is single-threaded by design |
| **Impact** | Medium — race conditions in multi-threaded test setups could cause flaky tests |
| **Category** | Operational (testing) |

A second risk is **drift between a fake and its Protocol**. A fake that is looser or stricter than the Protocol it stands in for lets a test pass, or fail, for a reason production never sees. The 0.3.0 audit found such drift: `InMemoryMessageSubscriber.simulate_message()` ignored an unknown topic though its docstring promised `KeyError`, and `FakeEventStore` refused the retry ADR-041 prescribes after a `ConcurrencyError`, created a stream on an empty append, and read from the end for a negative offset.

| Dimension | Assessment |
|-----------|------------|
| **Likelihood** | Medium — fakes change less often than the Protocols and ADRs they follow |
| **Impact** | Medium — adopters' tests pass against a fake and fail, or behave differently, against their production adapter |
| **Category** | Operational (testing) |

### Current Mitigation

- The constraint is documented in §2.1 and §10.4.
- All library tests run under single-threaded `asyncio` via `pytest-anyio`.
- The fakes are explicitly labelled as test-only (`pydomain.testing` module).
- Every fake runs its Protocol's `*Contract` suite (ADR-067), and a fix that restores a promise adds it to the suite (ADR-072), so adopters' adapters face the same cases as the fakes.

### Residual Risk

- **Users running tests with `anyio` backends other than `asyncio`** may encounter issues. pydomain's own tests run on asyncio only (the `anyio_backend` fixture in `tests/conftest.py`), and the contract suites pin asyncio unless a subclass overrides `anyio_backend` (ADR-067).
- **Multi-process test runners** (e.g., `pytest-xdist`) are safe because each process has its own fake instances. There is no shared state across processes by design.
- **Production use of fakes** (e.g., as an in-memory store in a prototype) would be incorrect — the fakes are not designed for concurrent access.

### Technical Debt

| Item | Status | Notes |
|------|--------|-------|
| No runtime guard against concurrent fake access | Open | A `threading.Lock` or deprecation warning for non-asyncio usage could be added, but the complexity is unjustified for test-only code. |
| Fakes that contradicted their docs or the Protocol's intent (FAKE-09, ES-03, ES-04, ES-05 (offsets)) | Resolved (ADR-072) | `simulate_message()` raises `KeyError` for an unknown topic; `FakeEventStore` records a `command_id` only when its append stores events, stores nothing on an empty append and raises `ValueError` for a negative `from_version`, pinned by `EventStoreContract` cases |
| Negative snapshot versions and `SubscriptionRunner` checkpoints are not validated (rest of ES-05) | Open | The library never writes a negative value, but a snapshot or checkpoint store that returns one now makes `EventSourcedRepository.get_by_id` or the runner's cycle raise `ValueError` from the event store, which also ends `run()` (ADR-072) |
| Fakes store objects by reference, with no serialization round trip | Open | A JSON round-trip event store for `pydomain.testing` is open as D-ES-16 (bug-fix plan §14); `InMemoryMessageSubscriber` also passes payloads by reference and keeps one handler per topic |

---

## 11.6 Architectural Drift and Layer Violations

### Risk

The strict module dependency graph (§2.5, §10.2) is the library's most important architectural invariant. As the codebase grows and more contributors are involved, accidental layer violations become more likely — a `cqrs/` file importing from `es/`, or `ddd/` importing infrastructure concepts.

| Dimension | Assessment |
|-----------|------------|
| **Likelihood** | Medium — grows with contributor count and codebase size |
| **Impact** | High — layer violations undermine modularity (quality goal G2) and may create circular dependencies |
| **Category** | Architectural |

### Current Mitigation

1. **`pytest-archon` architecture tests** (`tests/test_architecture.py`): 12 automated tests enforce the §2.5 dependency matrix at CI time. Every forbidden cross-module import direction (e.g. `ddd` → `cqrs`, `cqrs` → `es`, `es` → `infrastructure`) is covered. Violations are caught as test failures.
2. **Ruff `TID252` rule**: Bans relative imports, forcing explicit `pydomain.*` paths that make cross-module dependencies visible.
3. **Code review**: The `architect-reviewer` agent checks import paths during DDD boundary review.
4. **Clear documentation**: The dependency graph is documented in §2.5 and §10.2.

### Residual Risk

- **Circular imports** between `infrastructure/` and other modules are technically possible and would not be caught until import time (though `pytest-archon` would detect the import relationship).
- **`TYPE_CHECKING` imports** are not checked by default — the architecture tests run with full import analysis including type-checking blocks.

---

## 11.7 Projection Dual Abstraction Confusion

### Risk

The library provides two projection abstractions: `Projection[StateT]` (CQRS Protocol) and `EventSourcedProjection` (ES ABC). They serve different purposes (§9.3) but share similar names. Users may choose the wrong one or attempt to use them interchangeably.

| Dimension | Assessment |
|-----------|------------|
| **Likelihood** | Medium — naming overlap is confusing for newcomers |
| **Impact** | Low — using the wrong type results in a clear type error, not silent misbehaviour |
| **Category** | Usability |

### Current Mitigation

- Separated by module: `pydomain.cqrs.projection` vs `pydomain.es.projection`.
- Documented in §8.11 (concepts), §9.3 (design decision), and the Diátaxis template.
- Type system enforces the difference: `Projection` is a `Protocol` (no base class inheritance), `EventSourcedProjection` is an `ABC` (requires inheritance).

### Residual Risk

- **Import name collision**: Both modules export a class called `Projection` or `EventSourcedProjection`. A wildcard import (`from pydomain.cqrs.projection import *`) could shadow the other. Users must use explicit imports.
- **Documentation discoverability**: Users reading only the API reference may not find the conceptual explanation of when to use which.

---

## 11.8 Shared Repositories Across Concurrent Units of Work

### Risk

A repository buffers the events of the aggregates saved through it until a unit of work's commit pulls them, and a unit of work drains that buffer when its scope exits (ADR-068). The buffer belongs to the repository, not to a scope. When one repository is shared by units of work that run at the same time, one dispatch's commit or drain can take the other's buffered events: they are dropped, or published under the wrong command's tracing IDs. A probe with two concurrent dispatches on different aggregates, where the first fails, had the second publish both commands' events.

| Dimension | Assessment |
|-----------|------------|
| **Likelihood** | Medium — the docs and recipes share one in-memory repository across units of work, and a web application dispatches concurrently |
| **Impact** | High — events are lost or misattributed silently |
| **Category** | Operational (application wiring) |

### Current Mitigation

- The rule is documented in ADR-068, the unit-of-work concept page and the upgrade notes: share a repository only between units of work that run one after another; build the repository per unit of work when dispatches run concurrently (decision-log B-19).
- `AggregateLockingBehavior` does not make sharing safe: its locks are per aggregate key, so commands on different aggregates still share the buffer, and it releases them before the commit and the exit (CQRS-K3, fixed by W2-1). The docs say so.

### Residual Risk

- Nothing detects the shared wiring at runtime; an application that shares a repository and dispatches concurrently keeps the defect.

### Technical Debt

| Item | Status | Notes |
|------|--------|-------|
| Scope-keyed event buffers in shared repositories | Open | Backlog Feature F-6 (`docs/others/decision-log.md`); pairs with W2-1, which holds locks through the commit |

---

## 11.9 Summary

| ID | Risk | Likelihood | Impact | Mitigation Status |
|----|------|-----------|--------|-------------------|
| R-1 | Event schema breakage | High | Critical | Mitigated — `EventUpcaster` chain + unknown-key stripping + fallback. Residual: user must write upcasters. |
| R-2 | Saga state unbounded growth | Medium | Medium | Mitigated — `max_processed_events` / `max_step_history` caps + `prune_history()` + `SagaPruningPolicy` ClassVar auto-pruning (ADR-054). Residual: pruning vs. idempotency tradeoff. |
| R-3 | Pydantic v3 breaking changes | Medium | High | Monitored — pinned version floor, comprehensive tests. No action until v3 release. |
| R-4 | Thread-unsafe in-memory fakes | Low | Medium | Accepted — documented single-threaded `asyncio` constraint. Fakes are test-only. |
| R-5 | Architectural layer drift | Medium | High | **Mitigated** — `pytest-archon` architecture tests enforce all layer boundaries in CI. Ruff `TID252` + code review provide additional defence. |
| R-6 | Projection dual abstraction confusion | Medium | Low | Mitigated — separate modules + docs + type system. Residual: naming overlap for newcomers. |
| R-7 | A repository shared by concurrent units of work drops or misattributes events (§11.8) | Medium | High | Documented — share only between units of work that run one after another; build per unit of work otherwise. Scope-keyed buffers are Feature F-6. |

### Highest-priority items for future investment

1. **Pydantic v3 migration assessment** (R-3) — monitor the Pydantic roadmap and begin migration planning when v3 APIs stabilise.
