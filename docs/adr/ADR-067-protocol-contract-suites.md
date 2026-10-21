# ADR-067: Protocol Contract Suites in `pydomain.testing.contracts`

## Status

Accepted

## Date

2026-10-21

## Context

pydomain defines its storage and messaging boundaries as Protocols (ADR-001): `Repository`, `UnitOfWork`, `EventStore`, `SnapshotStore`, `CheckpointStore`, `OutboxStore`, `ProcessedMessageStore`, `LockProvider`, `SagaRepository`, `MessageBroker`, `MessageSubscriber` and `ProjectionStore`. A Protocol fixes method signatures; its behaviour lives only in docstrings and ADRs, such as "`get` returns `MISSING` for an unprocessed id" (ADR-018) or "an append with a stale `expected_version` raises `ConcurrencyError`" (ADR-041).

Four forces call for a shared, executable statement of those promises:

1. **Adopters write their own adapters.** A SQLAlchemy `Repository` or a Postgres `OutboxStore` is the adopter's code. Without a shared test, each adopter re-derives the promises from prose and tests a different subset of them.

2. **The shipped fakes are adapters too.** `pydomain.testing` ships a fake or in-memory adapter for every Protocol. The bug audit (`docs/others/bug-audit-plan.md`) found fakes that break their own Protocol, for example `FakeRepository.get_by_id` returning the stored object (FAKE-01), so a test that passes against a fake can fail against a real adapter. The fakes need the same test the adopter's adapter faces.

3. **pydomain has two runtime dependencies** (§2.2 of the arc42 constraints), and neither is a test framework. Whatever ships the tests must not make `pytest` a dependency of `import pydomain`.

4. **Some Protocols are generic over the adopter's domain.** A `Repository[Order, UUID]` stores the adopter's aggregate; the library cannot build an `Order`, nor change one.

## Decision

We will ship one reusable pytest suite per Protocol as public test API.

**1. Public test API: `pydomain.testing.contracts`.** The suites live in one subpackage of `pydomain.testing` and are exported from its `__init__`. They are documented in the how-to "Run a Contract Suite Against Your Adapter".

**2. One `*Contract` base class per Protocol.** `RepositoryContract`, `UnitOfWorkContract`, `EventStoreContract` and so on, one for each Protocol listed above. The adopter subclasses a suite in a `Test*` class; the base class's name ends in `Contract`, so pytest never collects it on its own.

**3. Cases assert only what the Protocol promises.** Each case comes from the Protocol's docstring or its governing ADR, and its name and docstring state the promise. Behaviour the Protocol leaves open is not tested, and a promise the suite does not cover yet is named in the suite's docstring (for example, `UnitOfWorkContract` does not yet check that exit without `commit` undoes the change; `SagaRepositoryContract` leaves out optimistic concurrency while the saga version convention is unspecified).

**4. `pytest` and `anyio` only through the optional `testing` extra.** `pip install "pydomain-lib[testing]"` adds `pytest>=8.0` and `anyio>=4.0`. Two archrules enforce the boundary: only `pydomain.testing.contracts` may import `pytest` or `anyio`, and no pydomain module may import `pydomain.testing.contracts`. A third archrule forbids the suites to import the fakes and in-memory adapters they test. A subprocess test checks that `import pydomain, pydomain.testing` loads neither `pytest`, `anyio` nor the suites.

**5. The adapter is supplied by overriding a fixture per role.** Each suite declares a fixture named after its role (`repository`, `event_store`, `broker`, …) that raises `NotImplementedError`; the subclass overrides it. Because it is a pytest fixture, the adapter can depend on the adopter's own fixtures (a database session), be `async`, and `yield` to clean up after each case.

**6. Domain inputs come from hook methods on the subclass.** Where a suite needs a value only the adopter can build, it calls a method the subclass overrides:

| Suite | Required hooks | Optional hooks (with defaults) |
|-------|----------------|-------------------------------|
| `RepositoryContract` | `new_aggregate()`: a never-saved aggregate with a pending event; `change(aggregate)`: a behaviour call that records another pending event | — |
| `UnitOfWorkContract` | `stage_change(uow)`: a change made through the unit of work, returning the number of events it records | — |
| `MessageSubscriberContract` | `deliver(subscriber, topic, payload)`: a message delivered through the adopter's broker | — |
| `EventStoreContract` | — | `new_event()`, `new_stream_id()` |
| `SagaRepositoryContract` | — | `new_state(**fields)` |
| `MessageBrokerContract` | — | `new_event()`; the `topic` class attribute |

`RepositoryContract` uses `change` to test optimistic concurrency as a lost update: two copies loaded at the same version are both changed, the first is saved, and saving the second raises `ConcurrencyError`. This holds for state-based and event-sourced repositories alike, which a case that saves an unchanged copy with a forged version does not.

**7. asyncio is pinned by default through an overridable `anyio_backend`.** Every case is an `anyio` test. Each suite inherits a session-scoped `anyio_backend` fixture that returns `"asyncio"`, so a `conftest.py` that parametrizes `anyio_backend` for the adopter's other tests does not reach the suites. The session scope matters because `anyio` makes every async fixture depend on `anyio_backend`: a narrower pin would reject an adapter fixture that uses a module- or session-scoped async fixture with `ScopeMismatch`. A subclass overrides `anyio_backend`, keeping the session scope, to run on another backend, or on several.

**8. The shipped fakes run every suite, and later fixes add their cases red first.** pydomain's own test suite runs each `*Contract` against its fake or in-memory adapter, and `RepositoryContract` also against `EventSourcedRepository` over `FakeEventStore`. Where a shipped adapter breaks a promise today, its run marks that case `xfail(strict=True)` with the issue that fixes it. A fix that makes a shipped adapter keep a new promise adds the suite case first, marked strict xfail, per the test-first workflow.

**9. Accepted coupling: the Repository and Saga suites read private aggregate state.** `RepositoryContract` reads `_pending_events` without draining it, to know which events `save` must hand over; `SagaRepositoryContract` calls `_add_event` to give a saga state an event. Both are the `PrivateAttr` buffer of ADR-050, and the suites live in the same package as `AggregateRoot`, so a change to the buffer changes the suites in the same commit.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Adapter supplied by a factory method (`def make_repository(self) -> Repository`) instead of a fixture | A plain method cannot request the adopter's fixtures (a database session or engine), cannot be `async` without the suite awaiting it in every case, and has no teardown. Adopters would wrap their fixtures by hand to reach the same result. |
| Suite-supplied sample domain types (a built-in `ContractOrder` aggregate) instead of hook methods | A typed adapter such as `SqlOrderRepository` stores only its own aggregate type and maps it to its own tables; a sample type the adapter cannot persist tests nothing. Hooks let the adapter be tested with the types it actually stores. Suites whose Protocol is not generic (`EventStore`, `MessageBroker`) keep a default and make the hook optional. |
| A separate `pydomain-contracts` package | Two packages to version and release in step: a Protocol change and its suite change would ship separately, and an adopter could pin incompatible versions. An optional extra keeps them in one release while the archrules keep pytest out of the runtime. |
| No shared suites: document the promises and let each adopter write tests | The status quo. Each adopter tests a different subset, the fakes are tested only by their own unit tests, and the fake-versus-real drift the audit found stays invisible. |

## Consequences

### Positive

- An adopter checks an adapter against every promise of its Protocol by overriding one fixture and, at most, three hooks.
- The shipped fakes face the same cases as adopters' adapters, so drift between a fake and its Protocol shows up as a failing or strict-xfail case with an issue attached.
- Promises become executable: a Protocol docstring change without a suite change, or the reverse, is visible in review.
- `import pydomain` and `import pydomain.testing` stay free of pytest, enforced by tests rather than by convention.

### Negative

- The suites are public API. Renaming a case, a fixture or a hook breaks adopters' test modules, so they follow the same stability rules as the rest of the library.
- Adopters must use pytest and anyio to use the suites; a project on another test runner gets no benefit.
- The Repository and Saga suites depend on private aggregate attributes (decision 9), so they cannot be lifted out of pydomain unchanged.
- A suite covers only what the Protocol promises today. Gaps (exit without `commit`, saga concurrency, tracing stamping) remain until the Protocols or the fakes are fixed, and an adapter can pass the suite while breaking a promise the suite does not yet check.

### Neutral

- `EventSourcedRepository` has no `delete` and so does not satisfy the `Repository` Protocol; its run marks the affected cases strict xfail until DCE-105 decides the fix.
- The `EventStore` duplicate-command case follows ADR-041 ("raises"), though the Protocol docstring says SHOULD; a store that does not deduplicate overrides that case.

## References

- `src/pydomain/testing/contracts/` — the suites and `_backend.py`
- `tests/testing/test_contracts_*.py` — the suites run against the shipped adapters
- `tests/test_architecture.py` — the archrules of decision 4
- `docs/diataxis/how-to/testing/run-contract-suites.md`
- `docs/others/bug-audit-plan.md` — FAKE-01, SAGA-07
- `docs/others/bug-fix-plan.md` — clusters W2-5 and W2-6, the test-first workflow
- ADR-001: Protocol over ABC for Interfaces
- ADR-018: MISSING Sentinel for Idempotency
- ADR-041: Optimistic Concurrency via `expected_version` + `command_id` Idempotency
- ADR-050: AggregateRoot `_pending_events` as `PrivateAttr`
