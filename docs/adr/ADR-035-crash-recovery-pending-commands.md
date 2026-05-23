# ADR-035: Crash Recovery via `pending_commands` Per-Command Tracking

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Sagas dispatch commands that trigger side effects (reserve inventory, charge payment, ship order). If the process crashes between dispatching a command and persisting the saga state, the system may:
1. **Lose the command** — the command was never dispatched.
2. **Duplicate the command** — the command was dispatched but state wasn't updated, so it gets dispatched again on recovery.

Both scenarios cause incorrect business behaviour. The saga manager needs to track which commands have been successfully dispatched so that crash recovery can resume from the correct point.

## Decision

Each command is tracked in `pending_commands` with a `dispatched` flag:

```python
class SagaState(AggregateRoot[UUID]):
    pending_commands: list[dict[str, Any]] = Field(default_factory=list)
```

Each entry contains:

```python
{
    "command_type": "ReserveItems",
    "module_name": "myapp.commands",
    "data": {...},
    "dispatched": False
}
```

The manager dispatches commands one at a time, marking each as dispatched and persisting state after each:

```python
async def _dispatch_and_persist_commands(self, state, commands):
    start = len(state.pending_commands) - len(commands)
    for i, cmd in enumerate(commands):
        await self.command_bus.dispatch(cmd)
        state.pending_commands[start + i]["dispatched"] = True
        await self.repository.save(state)
```

On crash recovery:
1. Load saga state from repository.
2. Scan `pending_commands` for entries with `dispatched == False`.
3. Re-dispatch only those unconfirmed commands.
4. Commands with `dispatched == True` are skipped (already processed).

This is an **at-least-once** guarantee — a command may be dispatched twice if the crash occurs between dispatch and state update. The idempotency layer (ADR-018) handles deduplication.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| No tracking (re-dispatch all) | Duplicates every command on recovery; relies entirely on downstream idempotency |
| Outbox pattern (separate table) | More reliable but adds infrastructure complexity; overkill for saga-internal tracking |
| Two-phase commit | Requires distributed transaction support; not available in many message brokers |

## Consequences

### Positive

- Crash recovery re-dispatches only unconfirmed commands — minimises duplicates.
- Per-command tracking is persisted atomically with saga state.
- Works with any command bus — no special infrastructure required.

### Negative

- At-least-once delivery — commands may be dispatched twice if crash occurs between dispatch and state update (mitigated by idempotency).
- Per-command persistence adds write amplification (one save per command).

### Neutral

- `dispatched` flag is a simple boolean — no distributed coordination needed.

## References

- `src/pydomain/cqrs/saga/manager.py` — `_dispatch_and_persist_commands()`, recovery logic
- `src/pydomain/cqrs/saga/state.py` — `SagaState.pending_commands`
- ADR-018: MISSING Sentinel for Idempotency
