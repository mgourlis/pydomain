# ADR-077: Saga Failure and Recovery Keep Every Command

## Status

Proposed. Amends [ADR-055](ADR-055-declarative-saga-failure-fail-true.md),
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
([decision log](../others/decision-log.md#12-library-behaviour) B-49 and B-83 to B-87).

1. **A failing step sends its forward command, then compensates** (B-83; amends ADR-055).
   `execute_compensations()` no longer discards the queued commands: it sets them aside, and
   `Saga.collect_commands_before_compensation()` hands them over. On the `COMPENSATING` path the manager
   sends them first, traced with the step's event as `causation_id`, then the compensations in LIFO order,
   traced with the saga id. This covers `fail=True` and ADR-055's imperative `dispatch(); await fail()`
   pattern alike, and `process_timeouts` for an `on_timeout()` that dispatches and then fails.
   - A failing forward command is recorded in `failed_compensations` with its exception's type as the error
     (ADR-071); the compensations continue, and the saga ends `FAILED`.
   - The forward commands are not checkpointed in `pending_commands` first. A crash between them and the
     compensations loses them, as it loses any compensation today (SAGA-09, W2-5).
   - Commands queued by a handler that then raises are still dropped: the manager discards them after the
     error path. Checkpointing them is SAGA-12, W2-5.
2. **An unhydratable pending command stays pending** (B-84; amends ADR-035). When recovery cannot hydrate a
   pending command (its module or class was renamed), it does not send it and leaves it in
   `pending_commands`, not dispatched; the commands it can hydrate are still sent. The attempt counts as one
   failed attempt (`retry_count` goes up once, through decision 3), the saga is suspended with the reason
   `Could not hydrate <CommandType>`, and an ERROR log on `pydomain.saga` names the command type and the
   saga id only (ADR-071). Nothing is cleared. Once the import path works again, the next recovery sends it;
   when `max_retries` runs out first, the saga fails as for any exhausted command. No schema change.
3. **`retry_count` goes up once per failed attempt.** `recover_pending_sagas` counts an attempt before it
   dispatches, as its docstring states, and `_redispatch_undispatched` no longer adds a second increment
   when that attempt fails. The event path still adds one per failed forward dispatch. Success resets the
   count to 0.
4. **`max_retries` is the number of retries after the first attempt, on both paths** (B-49, B-85). The
   field becomes `max_retries: int | None = 3`. The saga is out of retries once `retry_count >
   max_retries`; `None` skips the check. So `3` allows three retries, `0` allows none (the first failure is
   final) and `None` allows any number. `_process_saga` (on the next event) and `recover_pending_sagas` (on
   the next run) use the same rule. Exhausted retries still fail without compensating on the event path and
   with compensation in recovery (SAGA-11d, W2-5).
5. **Recovery continues past a failing saga** (B-86; amends ADR-035). `recover_pending_sagas` recovers each
   stalled saga in its own `try`: a failure is logged at ERROR with the saga id and the exception's type
   (the handler's own exception behind a `CommandExecutionError`), never its message, and recovery moves on.
   It returns a `list[UUID]` of the sagas not recovered in this run: a dispatch failed, a command could not
   be hydrated, or the saga type is not registered. A repository error from `find_stalled_sagas` itself
   still propagates.
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

The manager's dispatch, recovery and hydration log lines name the exception's type only. The compensation,
handler and timeout lines keep their format.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Re-queue the forward command on the declarative path only (B-83) | Rejected by the author on 2026-10-22: a forward failure would be labelled a failed compensation, its causation would be the saga id, and the imperative `dispatch(); await fail()` pattern would still lose its command |
| Defer `fail()` until the forward commands have gone through the checkpointed forward path (B-83) | Rejected by the author on 2026-10-22: a forward failure would reach the suspend-on-dispatch-failure path, which re-suspends a failing saga (SAGA-03) |
| Keep an unhydratable command with an error key, retried without limit (B-84) | Rejected by the author on 2026-10-22 |
| A new `SagaState.failed_commands` field for unhydratable commands (B-84) | Rejected by the author on 2026-10-22: a schema change for every adapter |
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
- No pending command disappears during recovery; a fixed import path is enough to send it.
- `max_retries` means the same on both paths, and the number is the number of retries; `None` gives an
  explicit "unlimited".
- One saga with a broken downstream no longer holds up the recovery of the others, or `process_timeouts`.
- Wiring the manager to a bus that drops events fails at construction instead of stalling every saga.
- An adapter written to the `SagaRepository` docs recovers interrupted compensations, and a repository
  hands each event over once.

### Negative

- Behaviour changes, each in the upgrade notes:
  - `max_retries=0` now fails a saga on the event path after one failed dispatch, where it retried forever;
  - the default `3` allows one more retry in recovery than before;
  - `recover_pending_sagas()` no longer raises a saga's dispatch error, and returns a list instead of `None`;
  - `SagaManager(..., command_bus=CommandBus())` raises `TypeError`;
  - a `fail=True` step now sends its command, which code may have worked around.
- A suspended saga with an unhydratable command is reported on every recovery run until the import path is
  fixed or its retries run out.
- The forward commands sent on failure are not checkpointed: a crash before they are sent loses them.

### Neutral

- Exhausted retries still fail without compensating on the event path and compensate on recovery
  (SAGA-11d, W2-5). Commands queued before a handler raises are still dropped (SAGA-12, W2-5).
- With the `MessageBus`, a saga command whose event re-enters the same saga still loses the inner save
  (SAGA-20, W2-5).

## References

- `src/pydomain/cqrs/saga/saga.py`: `Saga.on()`, `Saga.fail()`, `Saga.execute_compensations()`,
  `Saga.collect_commands_before_compensation()`
- `src/pydomain/cqrs/saga/manager.py`: `SagaManager`, `CommandDispatcher`, `EventHandlerRegistrar`
- `src/pydomain/cqrs/saga/state.py`: `SagaState.max_retries`
- `src/pydomain/cqrs/saga/repository.py`: `SagaRepository.find_stalled_sagas`
- `src/pydomain/testing/fake_saga_repository.py`, `src/pydomain/testing/contracts/saga_repository.py`
- `tests/saga/test_failure_and_recovery.py`, `tests/testing/test_contracts_saga_repository.py`
- `docs/diataxis/how-to/upgrade/upgrade-to-next-release.md`
- `docs/others/bug-audit-findings.md`: SAGA-01, SAGA-11a to SAGA-11c, SAGA-19, SAGA-21, FAKE-12; A29
- `docs/others/bug-fix-plan.md` §8 (W1-5), §14 (D-11c)
- `docs/others/decision-log.md` §1.2: B-49, B-83, B-84, B-85, B-86, B-87
- ADR-031: SagaManager as Separate Orchestrator (amended by this ADR)
- ADR-035: Crash Recovery via `pending_commands` Per-Command Tracking (amended by this ADR)
- ADR-055: Declarative Saga Failure via `fail=True` (amended by this ADR)
- ADR-067: Protocol Contract Suites in `pydomain.testing.contracts`
- ADR-071: Library-Owned Failure Paths Never Carry Payload Values
- YouTrack DCE-120
