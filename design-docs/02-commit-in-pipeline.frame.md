# Frame: Commit Inside the Command Pipeline

- **Date:** 2026-10-22
- **Issue:** DCE-142 (Feature DCE-130, fix plan cluster W2-1)
- **Alignment:** [02-commit-in-pipeline.alignment.md](02-commit-in-pipeline.alignment.md) (decided, B-103 to B-127)
- **Research:** [02-commit-in-pipeline.research.md](02-commit-in-pipeline.research.md)
- **ADR:** [ADR-079](../docs/adr/ADR-079-commit-inside-the-command-pipeline.md) (Proposed)

Each phase is one implementation Feature Task under DCE-130 and one PR. Every phase follows the fix plan's §4
and §5: test-first (strict-xfail red tests run locally with `--runxfail` and pushed with the green commit,
P-32), docs in the same change, an upgrade-note entry for each behaviour change (B-114). No phase starts before
ADR-079 is Accepted. The first phase adds the amendment notes to ADR-017 to 020 and the corrections to ADR-068
and ADR-076.

Test names below are the acceptance tests; the fixer may group them in classes but keeps the names. Every
test uses synthetic data and asserts through the public API (fix plan §4.3: no private asserts).

**Review scenarios to start from.** Review ⑨ of PR #124 ran a patched copy of the tree; its scripts may not
survive, so each is described here by what it asserts:

| Scenario | Phase | Asserts |
|---|---|---|
| Order, K1, K3, B-116, logging | P1 | A spy on store, locks and UoW hooks gives `get, acquire, handler, _flush, _write_outbox, _commit, release, set`; for each fail point a retry runs the handler again; two locked increments with an awaiting `_commit` end at 2; a cached duplicate commits nothing; `LoggingBehavior` logs "Failed" for a commit failure |
| K3 on `FakeRepository` | P1 | The same increment test is green before the fix when the repository shares objects by reference (it gives 2): the K3 test needs copy-on-read, write-on-commit storage |
| Nested order | P1 | Outer locks A and dispatches Inner on A (reentry error) and on B. Today: `... inner-handler:B, release:B, commit:inner, release:A, commit:outer`; designed: `... commit:inner, release:B, commit:outer, release:A` |
| Shield | P3 | `_commit`/`_rollback` finish under one and repeated cancellations; the cancellation is re-raised with `cancelling()` consistent; `asyncio.timeout` gives `TimeoutError` with the commit kept; inside a `TaskGroup`; with an eager task factory; `shield_hooks = False`; the B-109/B-121 holes (a cancel during the post-commit `set`, or during the lock release after the commit) |
| Pending cancel | P3 | A cancellation requested during `_write_outbox` and delivered at the next await: `_commit` still runs, `CancelledError` still reaches the caller (a before/after `cancelling()` comparison swallows it) |
| anyio spin | P3 | Under an anyio cancel scope over a 0.5 s commit, the task-plus-`asyncio.shield` loop alone spins (95127 iterations, 0.51 s CPU); the hybrid of B-122 must not |
| Task-bound storage | P3 | Storage keyed by `current_task()` sees another task in a shielded hook: rows committed `[]`, no error; a context variable set in `_commit` is invisible to the caller unless the hook task shares the caller's context (B-119) |
| Reservation | P4 | Duplicates with and without locks; a cancel during `set`; a TTL expiry followed by an unowned release; a cancel during `reserve`; a failed release |

---

## Phase 1: Tracer Bullet: the commit runs in the terminal step

**Feature Task title:** W2-1 P1: Commit in the pipeline's terminal step; locks held and result recorded after it

**Rows:** CQRS-K1 (the cached failed commit), CQRS-K3, CQRS-11, FAKE-04 (commit-failure injection).
Decisions B-103, B-104, B-105, B-112, B-113, B-116, B-118, B-127.

**Components:**

- `src/pydomain/cqrs/command_bus.py`: the bus's own terminal step runs the handler, `uow.commit()`, then sets
  `ctx.new_events` and the private committed mark (with the result, on success; ADR-079 point 6); the bus
  stops committing after the pipeline; `ctx.handler` stays the handler. `MessagePipeline`'s generic terminal
  is unchanged (14 `tests/cqrs/test_pipeline.py` tests run `kind=COMMAND` with no unit of work). The
  `dispatch` docstring (l.103) describes the new order.
- `src/pydomain/cqrs/behaviors.py`: docstrings of `IdempotencyBehavior` (records after the commit; the CQRS-04
  caveat stays until Phase 4) and `AggregateLockingBehavior` (held through the commit; l.349 "the command
  commits" becomes "the commit has already happened").
- `src/pydomain/cqrs/locking.py`: `LockProvider` docstring, a lease covers the commit.
- `src/pydomain/testing/fake_unit_of_work.py`: `fail_at: Literal["flush", "write_outbox", "commit"] | None`
  and `failure: BaseException | None = RuntimeError(...)`; the hook named by `fail_at` raises `failure` once,
  in the next scope that reaches it, then clears itself, like `FakeOutboxStore`'s `fail_next_append` (B-127).
- `docs/adr/`: amendment notes in ADR-017, 018, 019, 020; correction notes in ADR-068 (l.171-173) and ADR-076
  (l.74-75 and l.189) (B-113).
- Probes: delete `tests/support/probes/review/pr117/one_key_lock_k3.py` (it deadlocks by design under the fix:
  B waits for the lock A holds through its gated commit; the K3 tests replace it) and its row in
  `tests/support/probes/review/README.md` (l.15); in `tests/support/probes/README.md` mark CQRS-K1 and CQRS-K3
  (`cqrs/p_known.py`), CQRS-11 (`cqrs/p_10_14.py`) and the FAKE-04 commit-failure part
  (`fakes/probe_repo_uow.py`) fixed.

**Testing strategy** (fix plan §9.2: ordering spy; failure injection at every await of `commit()`):

- `test_command_pipeline_order_is_acquire_handler_commit_release_record`: the spy order above.
- `test_failed_commit_is_not_cached` (parametrized over `fail_at` = flush, write_outbox, commit): the retry
  with the same `command_id` runs the handler again; assert the handler's run count (2), not that the work
  persisted, because `FakeRepository` keeps saves after a rollback (W2-6).
- `test_locked_increments_do_not_lose_updates_across_a_slow_commit`: two gathered locked increments on one
  key, storage that copies on read and writes on commit, and an awaiting `_commit`, end at 2. The test must
  fail before the fix (it passes on `FakeRepository`, which shares by reference).
- `test_behavior_sees_committed_events_after_next`, and `test_dispatch_returns_the_events_behaviors_saw`:
  `dispatch()` returns the same stamped events as `ctx.new_events`.
- `test_pipeline_that_never_reaches_the_handler_commits_nothing`: a cached duplicate and a short-circuiting
  behavior leave the scope rolled back, with nothing committed (B-116). Update the existing
  `TestPipelineBehaviors.test_behavior_short_circuits` in `tests/cqrs/test_bus.py` (l.266; its
  `assert uow.hooks == ["commit"]` at l.295 pins the old commit).
- `test_nested_command_commits_and_releases_before_the_outer_commit`: the nested order above.
- `test_logging_behavior_reports_a_failed_commit_as_failed`: "Failed ...: <Type>", never "Completed".
- `test_log_names_the_registered_handler`: the terminal step does not rename the handler in logs.
- `test_fake_unit_of_work_fail_at_raises_from_each_hook`, `test_fake_unit_of_work_fail_at_fails_once_then_clears`,
  `test_fake_unit_of_work_failure_defaults_to_runtime_error` and `test_fake_unit_of_work_without_fail_at_commits`.

**Docs:**

- arc42: §4.5 S4 (diagram and table); §5.3.5; §6.1 with the new sequence diagram (commit inside the
  pipeline, release after it); §8.5 (nested diagram, slot table with the ordering promise of ADR-079 point 8);
  §10.6; §9.21 (l.723) and §9.27 (l.932), which repeat "released before the commit ... W2-1"; §11 l.254 and
  l.264 (F-6 "pairs with W2-1", B-113); §12 l.202 (Command Bus "On success: commits") and l.354
  (`new_events`); §10.4 l.208 and §5 l.895 (the `FakeUnitOfWork` rows: `fail_at`).
- Decision log: the F-6 backlog row (l.207), "pairs with W2-1" (B-113).
- Diataxis: `concepts/cqrs/pipeline-behaviors.md` (the ordering promise, with the short-circuit and
  committed-then-raised rows), `concepts/cqrs/command-bus.md`, `concepts/cqrs/idempotency-and-locking.md`
  (l.56-59 flow, l.72 locks, l.130 "still succeeds and commits"), `how-to/cqrs/add-idempotency.md` (l.103: the
  cached-failure limit goes; the at-least-once gap is named, crash and ambiguous commit failure),
  `how-to/cqrs/add-pipeline-behavior.md` (what "after `next()`" means), `how-to/cqrs/add-distributed-locking.md`
  (the lease note; l.174 "The command commits"), `how-to/testing/use-fake-uow.md` (`fail_at`),
  `concepts/infrastructure/message-broker.md` (l.120) and `how-to/cqrs/handle-domain-events.md` (l.57), which
  say "the bus commits".
- Upgrade notes: items 1, 2, 4 and 6 of ADR-079's list.

**Verification gate:**

- The ordering spy shows the commit before the release and the record, and the K3 test is red before the fix
  and gives 2 after it, through the public API.
- `make check` green; `tests/test_architecture.py` and the export tests pass.

**Acceptance criteria:**

- [ ] All Phase 1 tests pass; their red versions failed locally for the stated reason.
- [ ] No caller of `CommandBus` commits after the pipeline; ADR-020's "handlers never commit" still holds.
- [ ] The listed docs, probe changes and upgrade-note entries are in the PR.

---

## Phase 2: A second `next()` is refused

**Feature Task title:** W2-1 P2: Refuse a second next() in command pipelines (PipelineReentryError)

**Rows:** CQRS-21. Decision B-111.

**Components:**

- `src/pydomain/cqrs/exceptions.py`: `PipelineReentryError(CQRSError)`, message without content (ADR-071).
- `src/pydomain/cqrs/command_bus.py`: the terminal step counts its calls per dispatch and raises on the second,
  before the handler. Review ⑨ checked that refusing it breaks no existing test.
- `src/pydomain/cqrs/__init__.py`, `tests/test_package_exports.py`: the new name.

**Testing strategy:**

- `test_second_next_raises_pipeline_reentry_error`: a behavior calling `next()` twice; the handler runs once;
  the caller gets `CommandExecutionError` whose `__cause__` is `PipelineReentryError`.
- `test_second_next_after_a_commit_keeps_the_first_commit`: the first run's change is persisted and its
  scope is not rolled back.
- `test_retry_behavior_after_a_failed_handler_is_refused`: the retry gets `PipelineReentryError`, and the
  first run's events never commit.
- `test_query_and_event_pipelines_allow_a_second_next`: unchanged behaviour, pinned.

**Docs:**

- arc42: §5.3.5 (the rule); §5.3.10 (l.511, the exception list); the §5 overview diagram (l.286-287); §12
  (l.485, l.495), which lists the exceptions.
- Diataxis: `concepts/cqrs/pipeline-behaviors.md`, `how-to/cqrs/add-pipeline-behavior.md` ("call `next()` at
  most once in a command pipeline; retry by dispatching again"),
  `concepts/infrastructure/exception-hierarchy.md`, `api-reference/_index.md` (l.44).
- Upgrade notes: item 5.

**Verification gate:**

- The audit probe's `RetryOnce` scenario (`cqrs/p_15_25.py` `c21`) rewritten as a test commits one run's
  events or none, never two.

**Acceptance criteria:**

- [ ] All Phase 2 tests pass; the export test lists `PipelineReentryError`.
- [ ] `tests/support/probes/README.md` no longer lists CQRS-21 as open.

---

## Phase 3: The commit and the rollback are shielded

**Feature Task title:** W2-1 P3: Shield _commit and _rollback from cancellation; record a committed command

**Rows:** CQRS-07. Decisions B-108, B-109, B-110, B-115, B-118, B-119, B-121, B-122, B-123, B-124, B-126.

**Components:**

- `pyproject.toml`: `anyio>=4` in `dependencies` (B-122); the README, `docs/diataxis/introduction.md`,
  `docs/diataxis/getting-started/installation.md` and the arc42 pages that name `uuid-utils` as a
  dependency (§1, §2, §3, §5, §7, §8, §10, §11) say so.
- `src/pydomain/cqrs/unit_of_work.py`: one private helper (ADR-079 point 6) runs a call inside
  `anyio.CancelScope(shield=True)` and, under asyncio, in a task created in the caller's context and awaited
  through `asyncio.shield`, absorbing the caller's cancellations until the call ends, then re-raising; a
  `CancelledError` that reaches the caller while the inner task is not cancelled is the caller's and is never
  swallowed, also one pending at entry; a hook error during a cancellation becomes a type-only note
  (ADR-078). `_commit()` and `_rollback()` use it unless `shield_hooks` is `False`; a completed `_commit()` sets
  the scope committed before the cancellation is re-raised. Docstrings: `shield_hooks`, task-keyed storage
  (B-119), a Protocol-only UoW shields its own hooks.
- `src/pydomain/cqrs/command_bus.py`: the terminal step sets the committed mark also when `commit()` raises
  after a completed `_commit()` (read from the private flag of `AbstractUnitOfWork`).
- `src/pydomain/cqrs/behaviors.py`: `IdempotencyBehavior` records the result through the helper after every
  commit, and on any `BaseException` once the mark is set, then re-raises (B-121, B-123).
- `src/pydomain/cqrs/idempotency.py`: the `ProcessedMessageStore` docstring, a store works from any task
  (B-123).

The fixer verifies the hybrid of B-122 on Python 3.12, 3.13 and 3.14 and reports, without choosing another
design, if it fails.

**Testing strategy** (fix plan §9.2: cancellation at every await of `commit()`):

- `test_cancel_during_flush_or_outbox_rolls_back` (parametrized: `_flush`, `_write_outbox`): nothing
  committed, one rollback, `CancelledError` propagates (B-110).
- `test_cancel_pending_before_commit_still_commits_and_propagates`: the pending-cancel scenario.
- `test_cancel_during_commit_completes_it_and_does_not_roll_back`: the storage write is kept, the scope is
  committed, no rollback runs, `CancelledError` propagates.
- `test_repeated_cancellation_during_commit_is_absorbed_until_it_ends`.
- `test_timeout_during_commit_keeps_the_commit`: `asyncio.timeout` gives `TimeoutError`, `cancelling()` is 0
  afterwards, the commit is kept.
- `test_anyio_scope_cancel_during_commit_does_not_spin` (anyio marker): an anyio cancel scope over a slow
  commit; the helper's loop iterations (or CPU time) stay bounded.
- `test_cancel_during_rollback_completes_the_rollback`: a second cancellation while `_rollback` runs; it
  finishes, the buffers are drained, the cancellation propagates.
- `test_commit_hook_failure_during_cancellation_is_a_note`: `CancelledError` with
  `clean-up also failed: <Type>`, no content.
- `test_hook_raising_its_own_cancelled_error_is_a_failure`: with the caller not cancelled, the caller gets
  `CommandExecutionError` whose `__cause__` is the hook's `CancelledError`, its task's `cancelling()` is 0, and
  the scope is not marked committed (B-126).
- `test_hooks_run_under_trio_inline_with_the_anyio_shield`, or the hand-driven equivalent of the B-69 tests if
  trio stays out of the dev dependencies (ADR-076 l.148).
- `test_shielded_hooks_run_in_their_own_task_by_default`: `asyncio.current_task()` inside `_commit` and
  `_rollback` is not the dispatching task.
- `test_hooks_share_the_callers_context`: a context variable set before the dispatch is visible in the hooks,
  and one set in `_commit` is visible to the caller afterwards (B-119).
- `test_shield_hooks_false_runs_the_hooks_in_the_calling_task`, and
  `test_shield_hooks_false_leaves_the_hooks_cancellable`: a cancellation during `_commit` interrupts it and the
  scope rolls back.
- `test_cancelled_committed_command_is_recorded`: the retry gets the cached result and the handler does not
  run again (B-109).
- `test_cancel_during_the_post_commit_record_still_records` (B-123).
- `test_cancel_during_the_lock_release_after_the_commit_is_recorded` (B-121).
- `test_committed_then_cancelled_command_does_not_reach_in_process_handlers`: pins B-124.
- `test_anyio_is_a_runtime_dependency`: `pyproject.toml` lists `anyio>=4`.
- `UnitOfWorkContract`: a case for "a commit cancelled while its storage commit runs is committed", if the
  fixer finds it holds for any conforming adapter; otherwise the case stays in `tests/cqrs/`.

**Docs:**

- arc42: §4 l.98; §5.3.7 (unit of work); §6.1 failure modes (the cancellation row); §10.6; the dependency
  lists named above.
- Diataxis: `concepts/cqrs/command-bus.md` (l.71), `concepts/cqrs/unit-of-work.md`; the pages that override
  `_commit`/`_rollback` (`how-to/recipes/cqrs-with-ddd.md`, `how-to/recipes/all-modules.md`,
  `how-to/infrastructure/configure-outbound-event-gateway.md`, `concepts/infrastructure/outbound-event-gateway.md`
  l.43): the hooks run in their own task under asyncio, in the caller's context, and task-keyed storage
  (`async_scoped_session(scopefunc=current_task)`) sets `shield_hooks = False`, with a short example;
  `concepts/cqrs/idempotency-and-locking.md` and `how-to/cqrs/handle-domain-events.md`: a command that
  commits and is then cancelled reaches in-process handlers only through the outbox path (B-124).
- Upgrade notes: item 7, naming `shield_hooks` and the `anyio` dependency.

**Verification gate:**

- The scratch scenario "cancel at `_commit.after-db`" (research §2) gives `db_row=True`, no `_rollback`, and a
  cached result through the public API, and the anyio scenario stays within its iteration bound on 3.12 to 3.14.

**Acceptance criteria:**

- [ ] All Phase 3 tests pass; their red versions failed locally for the stated reason.
- [ ] `anyio>=4` is in `dependencies` and every page that lists the runtime dependencies says so.
- [ ] `shield_hooks` is documented on `AbstractUnitOfWork` and in the upgrade note.
- [ ] `tests/support/probes/README.md` no longer lists CQRS-07 as open.

---

## Phase 4: Commands are reserved before the handler

**Feature Task title:** W2-1 P4: MessageReservationStore and CommandInProgressError for concurrent duplicates

**Rows:** CQRS-04. Decisions B-106, B-107, B-117, B-118, B-120, B-121, B-123, B-125. Removes the P-25 caveat where a
reserving store is used.

**Components:**

- `src/pydomain/cqrs/idempotency.py`: `MessageReservationStore` Protocol (`reserve` returns a token or `None`;
  `release(message_id, token)`); docstring: a store works from any task (B-123).
- `src/pydomain/cqrs/exceptions.py`: `CommandInProgressError(CQRSError)`, naming the command type only.
- `src/pydomain/cqrs/behaviors.py`: `IdempotencyBehavior`'s reserving flow (ADR-079 point 4) when its store
  implements both Protocols; `release` with the token through Phase 3's helper when not committed; a failed
  `release` is a type-only note; `reserve` runs through the same helper, and a token it returned while the
  caller was cancelled is released at once before the cancellation is re-raised (B-125).
- `src/pydomain/testing/fake_processed_message_store.py`: implements `reserve`/`release` with tokens, in a key
  space separate from results and from `check_and_set`, with an optional TTL to test expiry.
- `src/pydomain/testing/contracts/message_reservation_store.py` *(new)*: `MessageReservationStoreContract`.
- Exports: `pydomain.cqrs`, `pydomain.testing`, `pydomain.testing.contracts`, `tests/test_package_exports.py`.

**Testing strategy** (fix plan §9.2: concurrent duplicate commands):

- `test_concurrent_duplicates_run_the_handler_once`: two gathered dispatches of one `command_id`, with and
  without lock keys; one runs, the other gets `CommandInProgressError` (wrapped), nothing of it commits.
- `test_duplicate_after_completion_gets_the_cached_result`.
- `test_failed_command_releases_its_reservation` (handler failure and commit failure through `fail_at`): the
  retry runs.
- `test_command_cancelled_before_the_commit_releases_its_reservation`.
- `test_cancelled_committed_command_completes_its_reservation`: not released; the retry gets the cached result
  (B-121).
- `test_release_after_a_ttl_expiry_does_not_free_a_newer_reservation` (B-120).
- `test_cancel_during_reserve_releases_the_new_token`: a cancellation while `reserve` runs; `reserve`
  completes, its token is released at once, the cancellation propagates, and a retry can reserve (B-125).
- `test_failed_release_is_a_note_on_the_error`, and the id stays blocked until the TTL (ADR-079 Negative).
- `test_store_without_reservation_keeps_get_and_set`: a `ProcessedMessageStore`-only store keeps the old flow.
- `MessageReservationStoreContract`: `test_reserve_returns_a_token_the_first_time`,
  `test_reserve_returns_none_while_reserved`, `test_release_with_the_token_allows_a_new_reservation`,
  `test_stale_token_does_not_release_a_newer_reservation`, `test_concurrent_reserve_admits_one_caller`,
  `test_reserved_id_is_not_a_result` (`get` returns `MISSING`), `test_set_completes_a_reservation`,
  `test_reservations_do_not_collide_with_check_and_set`; run against `FakeProcessedMessageStore`.

**Docs:**

- arc42: §5.3.5; §5.3.6 (the Protocol list); §5.3.10 (the exception list); §5 l.908 (the contract suites);
  §8.5 (mechanism column); §10.6 (duplicates: the known limit is gone with a reserving store); §12 glossary.
- Diataxis: `concepts/cqrs/idempotency-and-locking.md`, `how-to/cqrs/add-idempotency.md` (the Redis example
  gains `reserve` with `SET NX`, a TTL and a token value, and a compare-and-delete `release`; the CQRS-04 caveat
  stays only for stores without reservation; a store works from any task),
  `how-to/testing/use-fake-processed-message-store.md`, `how-to/testing/run-contract-suites.md` (l.29),
  `concepts/cqrs/pipeline-behaviors.md` (l.197 caveat), `concepts/infrastructure/exception-hierarchy.md`,
  `api-reference/_index.md` (l.42, l.44, l.96).
- Upgrade notes: item 3.

**Verification gate:**

- The audit probe scenario `documented(idem,lock)` (`cqrs/p_04_09.py`) rewritten as a test gives
  `handler runs=1` with `FakeProcessedMessageStore`.

**Acceptance criteria:**

- [ ] All Phase 4 tests and the new contract suite pass; the export tests list the new names.
- [ ] `ProcessedMessageStore`'s four methods are unchanged.
- [ ] `tests/support/probes/README.md` no longer lists CQRS-04 as open; scripts whose rows are all fixed are
  deleted (P-28).

---

## Phase Sequence

```mermaid
flowchart LR
  ADR[ADR-079 Accepted] --> P1[P1 commit in terminal step]
  P1 --> P2[P2 second next refused]
  P1 --> P3[P3 shielding]
  P3 --> P4[P4 reservation]
```

- P1 first: every later phase relies on the commit being inside `next()`.
- P2 and P3 can run in parallel after P1 (different code: the terminal step's call count versus the UoW
  hooks); both touch the terminal step lightly, so the second to merge rebases.
- P4 after P3: the protected `release` reuses P3's helper, and both change `IdempotencyBehavior`.

## Scope Boundaries

**Out:** F-6 (scope-keyed buffers); F-10 (a local outbox-fed dispatcher); W2-2 (event idempotency keys);
W2-10 (the tracing channel); W3-1 (error wrapping); W3-6 (exactly-once recording, 0.5.0); W2-6 (the
`FakeRepository` part of FAKE-04).

**Constraints on other clusters** (ADR-079 Neutral):

- W2-2 keeps the key spaces of ADR-079 point 4 separate, and reuses the reservation semantics without changing
  them.
- W2-10: a tracing behavior at slot 0 spans the commit, and W2-10's channel must be readable in the terminal
  step.
- W3-1 decides how `CommandInProgressError` and `PipelineReentryError` are wrapped.

## Phase status

| Phase | Feature Task | Status |
|---|---|---|
| P1 | to be created | not started |
| P2 | to be created | not started |
| P3 | to be created | not started |
| P4 | to be created | not started |
