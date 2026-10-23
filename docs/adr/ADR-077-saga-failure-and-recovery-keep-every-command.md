# ADR-077: Saga Failure and Recovery Keep Every Command

## Status

Accepted. Amends [ADR-055](ADR-055-declarative-saga-failure-fail-true.md),
[ADR-035](ADR-035-crash-recovery-pending-commands.md) and
[ADR-031](ADR-031-saga-manager-separate-orchestrator.md).

## Date

2026-10-21

## Context

The 0.3.0 audit (`docs/others/bug-audit-findings.md`, `docs/others/bug-audit-plan.md` §12, rows SAGA-01,
SAGA-11a to SAGA-11c, SAGA-19, SAGA-21, FAKE-12 and A29) found that the saga manager loses commands on its
failure and recovery paths, and that its contracts say less than the manager relies on:

1. **`fail=True` drops its forward command** (SAGA-01). ADR-055 says the mapped handler "dispatches the
   forward command (via `send`), then calls `await self.fail(reason=f_reason, compensate=True)`". `fail()`
   calls `execute_compensations()`, which clears every queued command before it queues the compensations
   (`saga.py:445-447`). The step's command is compensated but never sent: a bus received `ReserveItems,
   RefundPayment, CancelReservation`, without the `ChargePayment` the step declared. ADR-055's own headline
   example, notify the customer and then fail, never notifies.
2. **A pending command that cannot be hydrated is deleted** (SAGA-11a). `_redispatch_undispatched` skips a
   command that `hydrate_command` returns `None` for (a renamed module or class), then clears
   `pending_commands` once the others are sent. The command is lost with no trace in `failed_compensations`
   or `error`. ADR-035 promises that recovery re-dispatches every unconfirmed command.
3. **`retry_count` jumps** (SAGA-11b). `recover_pending_sagas` adds one before each attempt and
   `_redispatch_undispatched` adds another when the attempt fails, so one failed recovery counts twice
   (`1 → 3 → 5`). The `recover_pending_sagas` docstring says the count goes up once per recovery attempt.
4. **`max_retries=0` means two things** (SAGA-11c). `_process_saga` reads `0` as unlimited
   (`retry_count >= max_retries and max_retries > 0`); `recover_pending_sagas` reads it as "fail at once".
5. **Recovery aborts on the first failing saga** (SAGA-21). A dispatch failure in one saga propagates out of
   `recover_pending_sagas`, so every later stalled saga in the batch waits for the next run, and the loop the
   docs teach (`saga-error-handling.md`) never reaches `process_timeouts`.
6. **Wired to `CommandBus`, the manager drops events** (SAGA-19). The constructor is annotated
   `command_bus: CommandBus`, but `CommandBus.dispatch` returns the events a command raised instead of
   publishing them. With that wiring the events raised by saga-dispatched commands never reach any handler,
   the saga itself included; with a `MessageBus`, which publishes them, the saga advances.
7. **`FakeSagaRepository` re-emits stored events** (FAKE-12). `save` stores a deep copy before it calls
   `pull_events()`, so the stored copy keeps the drained events, and the next save of a loaded state hands
   them to `pull_events()` again, with the same `event_id`.
8. **The stalled-saga contract omits `COMPENSATING`** (A29). The `SagaRepository.find_stalled_sagas`
   docstring, `saga-repository.md` and the arc42 glossary say a stalled saga is one with pending commands.
   `recover_pending_sagas` also finishes a `COMPENSATING` saga a crash interrupted, which it can do only if
   the repository returns it. `FakeSagaRepository` does; an adapter written to the docs never would.

The state machine, optimistic concurrency and checkpointed compensation (SAGA-03 to SAGA-09, SAGA-11d,
SAGA-11e, SAGA-12, SAGA-17) are cluster W2-5, not this ADR.

## Decision

We will make the failure and recovery paths keep every command, and state in the contracts what the
manager relies on. The author decided each choice on 2026-10-21 and 2026-10-22
([decision log](../others/decision-log.md#12-library-behaviour) B-49, B-83 to B-87, B-91 to B-93 and B-95 to B-100; backlog F-9).

1. **A failing step sends its forward command, then compensates** (B-83; amends ADR-055).
   `execute_compensations()` no longer discards the queued commands: it sets them aside, and
   `Saga.collect_commands_before_compensation()` hands them over. On the `COMPENSATING` path the manager
   sends them first, traced with the step's event as `causation_id` (with the saga id on the timeout path,
   which has no event), then the compensations in LIFO order, traced with the saga id. This covers
   `fail=True` and ADR-055's imperative `dispatch(); await fail()` pattern alike, and `process_timeouts`
   for an `on_timeout()` that dispatches and then fails.
   - A failing forward command is recorded in `failed_compensations` with the type of its handler's own
     exception (the cause behind the bus's `CommandExecutionError`) as the error (ADR-071), and logged at
     ERROR as `Forward command <Type> failed during compensation of saga <id>: <ExceptionType>`; the
     compensations continue, and the saga ends `FAILED`. A failing compensation keeps storing the
     exception the dispatcher raised, `CommandExecutionError` through a bus; the two differ on purpose
     until W2-3 unifies them (backlog F-9).

     > **Amended by [ADR-081](ADR-081-handler-failures-reach-the-inbound-gateway.md) point 6 (B-186).** Takes effect with DCE-132 (W2-3). A failed compensation stores `describe(_failure_of(comp_err))`, its handler's own type, as a failed forward command does.

   - The forward commands are not checkpointed in `pending_commands` first. A crash between them and the
     compensations loses them, as it loses any compensation today (SAGA-09, W2-5).
   - When compensation runs, the commands queued by a handler (or an `on_timeout()`) that then raises are
     dropped: the manager discards them after the error path. Without a compensation stack, `fail()` sets
     `FAILED` without `execute_compensations()`, and those commands are still sent on the forward path;
     that, and checkpointing them, is SAGA-12, W2-5.
2. **An unhydratable pending command stays pending, and recovery stops there** (B-84, B-91, B-92; amends
   ADR-035). Recovery sends the pending commands in order. When it cannot hydrate one (its module or class
   was renamed), it does not send it, leaves it and every command after it in `pending_commands`, not
   dispatched, and stops: the commands before it were sent, the rest wait for the next run, so their causal
   order is kept (B-92). The attempt counts as one failed attempt (`retry_count` goes up once, through
   decision 3), and an ERROR log on `pydomain.saga` names the command type and the saga id only (ADR-071).
   - A saga that was not suspended is suspended with the reason `Could not hydrate <CommandType>`, naming
     the command that stopped recovery, and recovery marks that suspension as its own on the entry:
     `"suspended_by_recovery": True`, next to `"dispatched"` (B-95). No schema change. A saga already
     suspended keeps that suspension, its reason and `timeout_at` (B-91); recovery updates the reason
     only of a suspension it marked, and moves the mark to the entry that stops it now.
   - Once the import path works again, the next recovery sends the commands in order, clears
     `pending_commands` and resets `retry_count`. If the suspension carries recovery's mark, the saga is
     resumed: its status becomes `RUNNING` (also for a hand-built state that was still `PENDING`), so its
     next event is handled as before, also with `resumes_from` (B-91). Any other suspension stays, whatever
     its reason says: a user's suspension is never resumed or rewritten by recovery (B-95). The status is
     always `RUNNING`; recovery does not record and restore the exact earlier status (B-100).
   - An event that arrives while recovery's marked suspension holds the saga resumes it whatever
     `should_resume()` says, and clears the mark: that gate belongs to the user's suspensions (B-98). A
     "Dispatch failed" suspension keeps the gate (SAGA-04/05, W2-5).
   - When `max_retries` runs out first, the saga fails as for any exhausted command. No schema change.
3. **`retry_count` goes up once per failed attempt.** `recover_pending_sagas` counts an attempt before it
   dispatches, as its docstring states, and `_redispatch_undispatched` no longer adds a second increment
   when that attempt fails. The event path still adds one per failed forward dispatch. Success resets the
   count to 0.
4. **`max_retries` is the number of retries after the first attempt, on both paths** (B-49, B-85). The
   field becomes `max_retries: int | None = Field(default=3, ge=0)`: a negative value raises
   `ValidationError` (B-93). The check runs on construction and validation, including loading stored
   rows, so an adapter whose stored rows hold a negative value gets `ValidationError` on load; assignment to
   an existing state is not validated (B-97). The saga is out of retries once `retry_count > max_retries`; `None` skips the
   check. So `3` allows three retries, `0` allows none (the first failure is final) and `None` allows any
   number. `_process_saga` (on the next event) and `recover_pending_sagas` (on
   the next run) use the same rule. Exhausted retries still fail without compensating on the event path and
   with compensation in recovery (SAGA-11d, W2-5).
5. **Recovery continues past a failing saga** (B-86; amends ADR-035). `recover_pending_sagas` recovers each
   stalled saga in its own `try`: a failure is logged at ERROR with the saga id and the exception's type
   (the handler's own exception behind a `CommandExecutionError`), never its message, and recovery moves on.
   It returns a `list[UUID]` of the sagas not recovered in this run: a dispatch failed, a command could not
   be hydrated, the saga type is not registered, or its compensation ended `FAILED`. A repository error
   from `find_stalled_sagas` itself still propagates.
   - A `COMPENSATING` saga always ends through `_dispatch_compensations`, with or without a stack left, so
     one recovery ends it `COMPENSATED`, or `FAILED` when `failed_compensations` holds anything: a
     compensation that could not be hydrated, or a failure recorded before the crash. The returned list
     agrees: a saga that ends `FAILED` this way is in it (B-96). So is an exhausted saga that recovery fails
     and whose compensation then fails: any saga recovery ends `FAILED` with failed compensations is
     listed (B-99).
6. **The manager is typed with dispatcher Protocols** (B-87; amends ADR-031). `cqrs/saga/manager.py`
   defines `CommandDispatcher`, whose positional `async dispatch(command)` must run the command and publish
   the domain events it raised, and `EventHandlerRegistrar`, whose `register_event(event_type, handler)`
   `bind_to` calls. Both are exported from `pydomain`, `pydomain.cqrs` and `pydomain.cqrs.saga`. The
   `MessageBus` satisfies both. `SagaManager.__init__` raises `TypeError` for an instance of `CommandBus`,
   which returns the events instead of publishing them, with a message saying to pass the `MessageBus`. The
   keyword stays `command_bus`.
7. **A repository hands each event over once.** `FakeSagaRepository.save` drains the state's events before
   it stores the copy. `SagaRepositoryContract` gains
   `test_saving_a_loaded_state_does_not_re_emit_drained_events`.
8. **A stalled saga is one with pending commands, or one in `COMPENSATING`** (amends ADR-035). The
   `find_stalled_sagas` docstring, the docs and the glossary say so, and `SagaRepositoryContract` gains
   `test_find_stalled_sagas_returns_compensating_saga`.

The manager's dispatch, recovery, hydration and forward-command lines name the exception's type only. The
compensation, handler and timeout lines keep their format; ADR-071 leaves them to W2-3.

> **Amended by [ADR-081](ADR-081-handler-failures-reach-the-inbound-gateway.md) point 4.** Takes effect with DCE-132 (W2-3). The compensation, handler and timeout lines describe the handler's own exception by type, with a value-free stack.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Re-queue the forward command on the declarative path only (B-83) | Rejected by the author on 2026-10-22: its causation would be the saga id, and the imperative `dispatch(); await fail()` pattern would still lose its command |
| Defer `fail()` until the forward commands have gone through the checkpointed forward path (B-83) | Rejected by the author on 2026-10-22: a forward failure would reach the suspend-on-dispatch-failure path, which re-suspends a failing saga (SAGA-03) |
| Keep an unhydratable command with an error key, retried without limit (B-84) | Rejected by the author on 2026-10-22 |
| A new `SagaState.failed_commands` field for unhydratable commands (B-84) | Rejected by the author on 2026-10-22: a schema change for every adapter |
| Document the overwritten suspension and the stuck hydration suspension as SAGA-04, with a strict xfail for W2-5 (B-91) | Rejected by the author on 2026-10-22 |
| Recognise recovery's suspension by a documented, reserved reason prefix (B-95) | Rejected by the author on 2026-10-22: a user's reason that starts the same way would be resumed or rewritten |
| Defer the unhydratable compensation to W2-5 with a strict xfail (B-96) | Rejected by the author on 2026-10-22 |
| Store the cause's type for failed compensations too, now (F-9) | Rejected by the author on 2026-10-22: left to W2-3 |
| Validate `max_retries` on assignment too (B-97) | Rejected by the author on 2026-10-22 |
| Keep `should_resume()` for recovery's suspension and document the dropped event as SAGA-04/05 (B-98) | Rejected by the author on 2026-10-22 |
| Report only compensating sagas that end `FAILED`, and document the difference (B-99) | Rejected by the author on 2026-10-22 |
| Record and restore the exact status before a hydration suspension (B-100) | Rejected by the author on 2026-10-22 |
| Send the commands after an unhydratable one and document the reordering (B-92) | Rejected by the author on 2026-10-22: it breaks the causal order of a saga's commands |
| A negative `max_retries` means "already exhausted" (B-93) | Rejected by the author on 2026-10-22 |
| `max_retries=0` means unlimited on both paths (B-49) | Rejected by the author on 2026-10-21 |
| Reject `max_retries=0` with `ValueError` (B-49) | Rejected by the author on 2026-10-21 |
| `retry_count > 0 and retry_count >= max_retries` (B-85) | Rejected by the author on 2026-10-22: `0` and `1` would mean the same |
| Recovery logs failures and returns `None` (B-86) | Rejected by the author on 2026-10-22: the caller cannot tell which sagas need attention |
| Recovery raises an `ExceptionGroup` after the batch (B-86) | Rejected by the author on 2026-10-22: every caller's loop must catch it, or `process_timeouts` is skipped again |
| A `CommandDispatcher` Protocol for typing only (B-87) | Rejected by the author on 2026-10-22: a `CommandBus` matches it structurally, so the silent event loss stays possible |
| The manager publishes the events a `CommandBus` returns (B-87) | Rejected by the author on 2026-10-22: needs a second, event-publishing dependency |

## Consequences

### Positive

- A failing step's declared command is sent, as ADR-055 promised, in both saga styles.
- No pending command disappears during recovery, and recovery keeps the commands' order; a fixed import
  path is enough to send them and to end the hydration suspension.
- `max_retries` means the same on both paths, and the number is the number of retries; `None` gives an
  explicit "unlimited".
- One saga with a broken downstream no longer holds up the recovery of the others, or `process_timeouts`.
- Wiring the manager to a bus that drops events fails at construction instead of stalling every saga.
- An adapter written to the `SagaRepository` docs recovers interrupted compensations, and a repository
  hands each event over once.

### Negative

- Behaviour changes, each in the upgrade notes:
  - `max_retries=0` now fails a saga on the event path after one failed dispatch, where it retried forever.
    In recovery it still fails a saga whose dispatch failed, without another attempt; a saga that stalled
    without a failure (`retry_count` 0, a crash between save and dispatch) now gets one attempt, where it
    was failed at once;
  - with the default `3`, a command whose dispatch keeps failing is now tried 4 times in all (the first
    dispatch and 3 recovery attempts), where it was tried twice (one recovery attempt, because a failed
    attempt counted twice); from `retry_count` 0, recovery makes 4 attempts where it made 2. In general
    `N` now allows `N` retries;
  - a negative `max_retries` raises `ValidationError`;
  - recovery stops at an unhydratable command, so the commands after it wait, where they were sent;
  - `recover_pending_sagas()` no longer raises a saga's dispatch error, and returns a list instead of `None`;
  - `SagaManager(..., command_bus=CommandBus())` raises `TypeError`;
  - a `fail=True` step now sends its command, which code may have worked around.
- A suspended saga with an unhydratable command is reported on every recovery run until the import path is
  fixed or its retries run out.
- The forward commands sent on failure are not checkpointed: a crash before they are sent loses them.

### Neutral

- Exhausted retries still fail without compensating on the event path and compensate on recovery
  (SAGA-11d, W2-5). With no compensation to run, a handler's commands queued before it raised are still
  sent (SAGA-12, W2-5; a strict xfail pins the intended behaviour).
- With the `MessageBus`, a saga command whose event re-enters the same saga still loses the inner save
  (SAGA-20, W2-5). The same re-entry lets a `fail=True` step's forward command drive a forward step while
  the saga compensates: its event reaches the saga before the compensations are sent, and the saga, not
  yet saved as `COMPENSATING`, handles it (SAGA-20, W2-5; a strict xfail).
- An event can complete a saga suspended for an unhydratable command, which leaves that command pending in a
  terminal saga that recovery no longer finds (SAGA-04, SAGA-05, W2-5; a strict xfail).

## References

- `src/pydomain/cqrs/saga/saga.py`: `Saga.on()`, `Saga.fail()`, `Saga.execute_compensations()`,
  `Saga.collect_commands_before_compensation()`
- `src/pydomain/cqrs/saga/manager.py`: `SagaManager`, `CommandDispatcher`, `EventHandlerRegistrar`
- `src/pydomain/cqrs/saga/state.py`: `SagaState.max_retries`
- `src/pydomain/cqrs/saga/repository.py`: `SagaRepository.find_stalled_sagas`
- `src/pydomain/testing/fake_saga_repository.py`, `src/pydomain/testing/contracts/saga_repository.py`
- `tests/saga/test_failure_and_recovery.py`, `tests/saga/test_failure_and_recovery_edges.py`,
  `tests/testing/test_contracts_saga_repository.py`
- `docs/diataxis/how-to/upgrade/upgrade-to-0.3.1.md`
- `docs/others/bug-audit-findings.md`: SAGA-01, SAGA-11a to SAGA-11c, SAGA-19, SAGA-21, FAKE-12; A29
- `docs/others/bug-fix-plan.md` §8 (W1-5), §14 (D-11c)
- `docs/others/decision-log.md` §1.2: B-49, B-83 to B-87, B-91 to B-93, B-95 to B-100; §2: F-9
- ADR-031: SagaManager as Separate Orchestrator (amended by this ADR)
- ADR-035: Crash Recovery via `pending_commands` Per-Command Tracking (amended by this ADR)
- ADR-055: Declarative Saga Failure via `fail=True` (amended by this ADR)
- ADR-067: Protocol Contract Suites in `pydomain.testing.contracts`
- ADR-071: Library-Owned Failure Paths Never Carry Payload Values
- YouTrack DCE-120
