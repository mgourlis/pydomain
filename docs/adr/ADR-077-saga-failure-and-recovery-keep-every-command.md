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
manager relies on.

1. **`fail=True` sends its forward command, then compensates** (amends ADR-055). The step's forward
   command reaches the bus before the compensations, which follow in LIFO order.
   _Open: how the forward command is kept and dispatched (see Open decisions, O-1)._
2. **An unhydratable pending command stays recorded** (amends ADR-035). Recovery never deletes a pending
   command it could not send. _Open: how it is recorded as failed (O-2)._
3. **`retry_count` goes up once per failed attempt.** Recovery keeps the increment it saves before each
   attempt, as the `recover_pending_sagas` docstring states, and drops the second increment on failure.
4. **`max_retries=0` means no retries, on both paths, and `None` means unlimited** (decision log B-49,
   D-11c). The field becomes `max_retries: int | None = 3`. The first attempt is always made; `0` refuses
   every retry, on the handle path (`_process_saga`) as on recovery (`recover_pending_sagas`). Today `0` is
   unlimited on the handle path, so this is a behaviour change with an upgrade note.
   _Open: the comparison for `max_retries > 0` (O-3)._
5. **Recovery continues past a failing saga** (amends ADR-035). One saga's failure does not stop the
   others in the batch. _Open: how the failure is reported (O-4)._
6. **The manager is typed with a dispatcher Protocol** (amends ADR-031). _Open: its name, shape and what
   happens with a `CommandBus` (O-5)._
7. **A repository hands each event over once.** `FakeSagaRepository.save` drains the state's events before
   it stores the copy. `SagaRepositoryContract` gains
   `test_saving_a_loaded_state_does_not_re_emit_drained_events`.
8. **A stalled saga is one with pending commands, or one in `COMPENSATING`** (amends ADR-035). The
   `find_stalled_sagas` docstring, the docs and the glossary say so, and `SagaRepositoryContract` gains
   `test_find_stalled_sagas_returns_compensating_saga`.

## Open decisions

To be settled by the author and recorded in the decision log before this ADR is accepted: O-1 forward
command mechanism, O-2 recording an unhydratable command, O-3 the `max_retries` comparison, O-4 reporting
recovery failures, O-5 the dispatcher Protocol.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| `max_retries=0` means unlimited on both paths (B-49) | Rejected by the author on 2026-10-21 |
| Reject `max_retries=0` with `ValueError` (B-49) | Rejected by the author on 2026-10-21 |

## Consequences

### Positive

- A failing step's declared command is sent, as ADR-055 promised.
- No pending command disappears during recovery.
- `max_retries` means the same on both paths; `None` gives an explicit "unlimited".
- One saga with a broken downstream no longer holds up the recovery of the others.
- An adapter written to the `SagaRepository` docs recovers interrupted compensations.

### Negative

- `max_retries=0` now fails a saga on the handle path after one failed dispatch, where it used to retry
  forever; code that relied on that must set `max_retries=None`.

### Neutral

- Exhausted retries still fail without compensating on the handle path and compensate on recovery
  (SAGA-11d, W2-5).

## References

- `src/pydomain/cqrs/saga/saga.py`: `Saga.on()`, `Saga.fail()`, `Saga.execute_compensations()`
- `src/pydomain/cqrs/saga/manager.py`: `SagaManager`
- `src/pydomain/cqrs/saga/state.py`: `SagaState.max_retries`
- `src/pydomain/cqrs/saga/repository.py`: `SagaRepository.find_stalled_sagas`
- `src/pydomain/testing/fake_saga_repository.py`, `src/pydomain/testing/contracts/saga_repository.py`
- `tests/saga/test_failure_and_recovery.py`, `tests/testing/test_contracts_saga_repository.py`
- `docs/others/bug-audit-findings.md`: SAGA-01, SAGA-11a to SAGA-11c, SAGA-19, SAGA-21, FAKE-12; A29
- `docs/others/bug-fix-plan.md` §8 (W1-5), §14 (D-11c)
- `docs/others/decision-log.md` §1.2: B-49
- ADR-031: SagaManager as Separate Orchestrator (amended by this ADR)
- ADR-035: Crash Recovery via `pending_commands` Per-Command Tracking (amended by this ADR)
- ADR-055: Declarative Saga Failure via `fail=True` (amended by this ADR)
- ADR-067: Protocol Contract Suites in `pydomain.testing.contracts`
- ADR-071: Library-Owned Failure Paths Never Carry Payload Values
- YouTrack DCE-120
