# 6. Runtime View

This section documents the key runtime scenarios of the `pydomain` library — how building blocks interact at runtime to fulfil the main use cases. Each scenario is illustrated with an ASCII sequence diagram and a step-by-step walkthrough referencing the actual classes and methods involved.

---

## 6.1 Command Handling — Write Path

The primary write path: a command flows from the `MessageBus` through the `CommandBus`, pipeline behaviors, a handler, the `UnitOfWork`, and back out as domain events dispatched to registered event handlers.

### Sequence diagram

```
┌──────────┐   ┌────────────┐   ┌────────────┐   ┌───────────┐   ┌──────────┐   ┌──────────────┐
│  Client   │   │ MessageBus │   │ CommandBus │   │ Pipeline  │   │  UoW     │   │   Event      │
│           │   │            │   │            │   │ Behaviors │   │          │   │   Handlers   │
└─────┬─────┘   └─────┬──────┘   └─────┬──────┘   └─────┬─────┘   └────┬─────┘   └──────┬───────┘
      │               │                │                 │              │                │
      │ 1. dispatch(cmd)               │                 │              │                │
      ├──────────────►│                │                 │              │                │
      │               │                │                 │              │                │
      │               │ 2. isinstance(Command) == True   │              │                │
      │               │                │                 │              │                │
      │               │ 3. command_bus.dispatch(cmd)     │              │                │
      │               ├───────────────►│                 │              │                │
      │               │                │                 │              │                │
      │               │                │ 4. uow = uow_factory()        │                │
      │               │                ├───────────────────────────────►│                │
      │               │                │                 │              │                │
      │               │                │ 5. resolve tracing IDs         │                │
      │               │                │    (correlation_id, causation_id)              │
      │               │                │                 │              │                │
      │               │                │ 6. MessageContext(kind=COMMAND) │               │
      │               │                │                 │              │                │
      │               │                │ 7. async with uow:             │                │
      │               │                ├───────────────────────────────►│                │
      │               │                │                 │              │                │
      │               │                │ 8. pipeline.execute(ctx, cmd)  │               │
      │               │                ├───────────────►│               │                │
      │               │                │                 │              │                │
      │               │                │                 │ ┌────────────┴─────────────┐   │
      │               │                │                 │ │ 8a. Behavior chain:      │   │
      │               │                │                 │ │   LoggingBehavior        │   │
      │               │                │                 │ │     ValidationBehavior   │   │
      │               │                │                 │ │       IdempotencyBehavior│   │
      │               │                │                 │ │         LockingBehavior  │   │
      │               │                │                 │ │           handler(cmd,uow)│  │
      │               │                │                 │ └────────────┬─────────────┘   │
      │               │                │                 │              │                │
      │               │                │                 │              │ 8b. handler:    │
      │               │                │                 │              │  uow.repo.get() │
      │               │                │                 │              │  agg.method()   │
      │               │                │                 │              │                │
      │               │                │ 9. uow.commit() │              │                │
      │               │                ├───────────────────────────────►│                │
      │               │                │                 │              │                │
      │               │                │                 │              │ ┌──────────────┴┐
      │               │                │                 │              │ │ 9a. _flush()  │
      │               │                │                 │              │ │ 9b. _collect  │
      │               │                │                 │              │ │     _and_stamp│
      │               │                │                 │              │ │ 9c. _write_   │
      │               │                │                 │              │ │     outbox()  │
      │               │                │                 │              │ │ 9d. _commit() │
      │               │                │                 │              │ └──────────────┬┘
      │               │                │                 │              │                │
      │               │                │ 10. (result, events)           │                │
      │               │◄───────────────┤                 │              │                │
      │               │                │                 │              │                │
      │               │ 11. _dispatch_events(events)     │              │                │
      │               ├───────────────────────────────────────────────────────────────►│
      │               │                │                 │              │                │
      │               │                │                 │              │ 12. For each event:
      │               │                │                 │              │     for each handler:
      │               │                │                 │              │     pipeline.execute(ctx)
      │               │                │                 │              │     (fail independently)
      │               │                │                 │              │                │
      │  result       │                │                 │              │                │
      │◄──────────────┤                │                 │              │                │
      │               │                │                 │              │                │
```

### Walkthrough

| Step | Component | Action | Key code |
|------|-----------|--------|----------|
| 1 | Client | Calls `MessageBus.dispatch(cmd)` | `infrastructure/message_bus.py` |
| 2 | MessageBus | Detects `isinstance(cmd, Command)` → routes to CommandBus | `dispatch()` |
| 3 | CommandBus | Calls `command_bus.dispatch(cmd)` | `cqrs/command_bus.py` |
| 4 | CommandBus | Creates a new `UnitOfWork` from the registered factory | `uow = self._uow_factory()` |
| 5 | CommandBus | Resolves tracing IDs: `correlation_id` from command (or fallback to `command_id`); `causation_id` from command | `command_bus.py` |
| 6 | CommandBus | Creates `MessageContext(kind=COMMAND, uow=uow, ...)` | `cqrs/behaviors.py` — `MessageContext` |
| 7 | CommandBus | Enters `async with uow:` — scope-bound lifecycle | `cqrs/unit_of_work.py` |
| 8 | CommandBus | Calls `pipeline.execute(ctx, cmd)` | `cqrs/behaviors.py` — `MessagePipeline` |
| 8a | Pipeline | Runs behavior chain in onion order: `LoggingBehavior` → `ValidationBehavior` → `IdempotencyBehavior` → `AggregateLockingBehavior` → terminal handler | `behaviors.py` |
| 8b | Handler | Application-defined: loads aggregate via `uow.repo.get_by_id()`, calls a mutation method, aggregate records domain events internally | User code |
| 9 | CommandBus | Calls `uow.commit()` | `cqrs/unit_of_work.py` |
| 9a | UoW | `_flush()` — persists changes to storage | Overridable hook |
| 9b | UoW | `_collect_and_stamp()` — pulls events from all repos, stamps each with `correlation_id` / `causation_id` via `event.stamp()` | `unit_of_work.py` |
| 9c | UoW | `_write_outbox()` — writes events to outbox (if applicable) | Overridable hook |
| 9d | UoW | `_commit()` — commits the underlying transaction | Overridable hook |
| 10 | CommandBus | Returns `(result, events)` to MessageBus | `command_bus.py` |
| 11 | MessageBus | Calls `_dispatch_events(events)` | `message_bus.py` |
| 12 | MessageBus | For each event, for each registered handler: creates a new `MessageContext(kind=EVENT, uow=None)` and runs the handler pipeline. **Handlers fail independently** — one handler's exception is logged and swallowed, remaining handlers continue | `_dispatch_event()` |

### Failure modes

| Failure point | Behaviour |
|---------------|-----------|
| Handler raises | UoW `__aexit__` calls `rollback()` → events are lost → exception re-raised as `CommandExecutionError` |
| UoW commit fails | `rollback()` called → exception propagates |
| Event handler fails | Logged and swallowed — remaining handlers and other events continue unaffected |

---

## 6.2 Query Handling — Read Path

The read path: a query flows from the `MessageBus` through the `QueryBus` directly to a handler. No `UnitOfWork`, no side effects, no events.

### Sequence diagram

```
┌──────────┐   ┌────────────┐   ┌──────────┐   ┌───────────┐
│  Client   │   │ MessageBus │   │ QueryBus │   │  Handler  │
└─────┬─────┘   └─────┬──────┘   └────┬──────┘└─────┬──────┘
      │               │               │              │
      │ 1. dispatch(query)            │              │
      ├──────────────►│               │              │
      │               │               │              │
      │               │ 2. isinstance(Query) == True │
      │               │               │              │
      │               │ 3. query_bus.dispatch(query) │
      │               ├──────────────►│              │
      │               │               │              │
      │               │               │ 4. MessageContext(kind=QUERY, uow=None)
      │               │               │              │
      │               │               │ 5. pipeline.execute(ctx, query)
      │               │               ├──────────────┤
      │               │               │              │
      │               │               │              │ ┌──────────────────┐
      │               │               │              │ │ LoggingBehavior   │
      │               │               │              │ │   handler(query)  │
      │               │               │              │ │     (no uow arg)  │
      │               │               │              │ └──────────────────┘
      │               │               │              │
      │               │  result       │              │
      │               │◄──────────────┤              │
      │               │               │              │
      │  result       │               │              │
      │◄──────────────┤               │              │
      │               │               │              │
```

### Walkthrough

| Step | Component | Action | Key code |
|------|-----------|--------|----------|
| 1 | Client | Calls `MessageBus.dispatch(query)` | `infrastructure/message_bus.py` |
| 2 | MessageBus | Detects `isinstance(query, Query)` → routes to QueryBus | `dispatch()` |
| 3 | QueryBus | Looks up the registered pipeline for the query type | `cqrs/query_bus.py` |
| 4 | QueryBus | Creates `MessageContext(kind=QUERY, uow=None)` | `cqrs/behaviors.py` |
| 5 | QueryBus | Calls `pipeline.execute(ctx, query)` | `behaviors.py` |
| 5a | Pipeline | Runs behavior chain, then terminal handler with **single argument** (`query` only — no UoW passed for QUERY kind) | `MessagePipeline.execute()` |
| 6 | Handler | Returns typed `QueryResult` — may read from a read store, raw SQL, or any read-optimised source | User code |

### Key differences from command path

| Aspect | Command | Query |
|--------|---------|-------|
| UoW | Created per dispatch, committed on success | **Never created** (`uow=None`) |
| Terminal handler signature | `handler(cmd, uow)` | `handler(query)` |
| Side effects | Aggregate mutation + event publishing | **None** |
| Events | Collected and dispatched after commit | **No events** |
| Pipeline behaviors | Full chain (validation, idempotency, locking) | Logging only (typically) |

---

## 6.3 Event Replay — Aggregate Reconstitution

How an `EventSourcedAggregateRoot` is rebuilt from its persisted event stream. Covers snapshot-first hydration, upcasting, and the replay loop.

### Sequence diagram

```
┌──────────┐   ┌───────────────────┐   ┌───────────────┐   ┌────────────────┐   ┌──────────────┐
│  Repo    │   │ SnapshotStore     │   │  EventStore   │   │ Upcaster       │   │ EventSourced │
│  get_    │   │                   │   │               │   │ Registry       │   │ Aggregate    │
│  by_id() │   │                   │   │               │   │                │   │              │
└────┬─────┘   └────────┬──────────┘   └──────┬────────┘   └───────┬────────┘   └──────┬───────┘
     │                  │                     │                     │                   │
     │ 1. get_by_id(id) │                     │                     │                   │
     │                  │                     │                     │                   │
     │ 2. snapshot = store.get(type, id)      │                     │                   │
     ├─────────────────►│                     │                     │                   │
     │                  │                     │                     │                   │
     │ 3a. snapshot found?                     │                     │                   │
     │     YES ─────────┐                     │                     │                   │
     │                  │                     │                     │                   │
     │ 4. aggregate = cls(id=id)              │                     │                   │
     │     set fields from snapshot.state     │                     │                   │
     │     aggregate.version = snapshot.version│                    │                   │
     │                  │                     │                     │                   │
     │ 5. stream = read_stream(id, from_version=snapshot.version)  │                   │
     ├─────────────────────────────────────────►│                    │                   │
     │                  │                     │                     │                   │
     │ 3b. no snapshot  │                     │                     │                   │
     │     fallback to full replay            │                     │                   │
     │                  │                     │                     │                   │
     │ 5'. stream = read_stream(id, from_version=0)                 │                   │
     ├─────────────────────────────────────────►│                    │                   │
     │                  │                     │                     │                   │
     │ 6. For each event in stream:           │                     │                   │
     │    (upcasting handled by EventRegistry │                     │                   │
     │     at deserialization time)           │                     │                   │
     │                  │                     │                     │                   │
     │                  │                     │                     │                   │
     │ 7. aggregate._replay(event)            │                     │                   │
     ├─────────────────────────────────────────────────────────────────────────────────►│
     │                  │                     │                     │                   │
     │                  │                     │                     │    ┌──────────────┴┐
     │                  │                     │                     │    │ 7a. _when(evt) │
     │                  │                     │                     │    │ 7b. version++  │
     │                  │                     │                     │    │ (no buffering) │
     │                  │                     │                     │    └──────────────┬┘
     │                  │                     │                     │                   │
     │ 8. return aggregate                    │                     │                   │
     │                  │                     │                     │                   │
```

### Walkthrough

| Step | Component | Action | Key code |
|------|-----------|--------|----------|
| 1 | `EventSourcedRepository` | `get_by_id(id_)` is called | `es/event_sourced_repository.py` |
| 2 | Repository | Asks `SnapshotStore` for a snapshot of the aggregate | `store.get(aggregate_type, aggregate_id)` |
| 3a | Repository | If snapshot found: creates aggregate instance, restores fields from `snapshot.state`, sets `version = snapshot.version` | `get_by_id()` — snapshot branch |
| 3b | Repository | If no snapshot: falls through to full replay from version 0 | `get_by_id()` — fallback branch |
| 5 | Repository | Reads event stream from `EventStore`, starting at snapshot version (or 0) | `event_store.read_stream(id, from_version)` |
| 6 | `EventRegistry` | During deserialization, `UpcasterRegistry.resolve(type, version)` chains transforms to migrate old events to current schema | `infrastructure/event_registry.py` — `deserialize()` |
| 7 | Repository | Calls `aggregate._replay(event)` for each event | `es/aggregate.py` |
| 7a | Aggregate | `_when(event)` dispatches by `isinstance` to mutate aggregate fields | Subclass implementation |
| 7b | Aggregate | Increments `self.version += 1` — **no event buffering** (unlike `_apply()`) | `_replay()` |
| 8 | Repository | Returns fully reconstituted aggregate | `get_by_id()` |

### Snapshot-first vs full replay

| Path | Starting point | Events replayed | Performance |
|------|---------------|-----------------|-------------|
| Snapshot-first | Snapshot state + version | Only events after snapshot | Fast — skips historical events |
| Full replay | Empty aggregate, version 0 | All events in stream | Slower — grows with stream length |

### `_apply()` vs `_replay()` distinction

| Method | Mutates state | Buffers event | Increments version |
|--------|:------------:|:-------------:|:-----------------:|
| `_apply(event)` | ✅ `_when()` | ✅ `_add_event()` | ✅ |
| `_replay(event)` | ✅ `_when()` | ❌ | ✅ |

---

## 6.4 Saga Orchestration — Event-Driven Choreography

How an incoming domain event triggers saga creation or continuation, produces commands, and handles failures with compensation.

### Sequence diagram

```
┌──────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ MessageBus│  │ SagaManager  │   │ SagaRegistry │   │ SagaRepo │   │   Saga   │   │CommandBus│
└─────┬─────┘   └──────┬───────┘   └──────┬───────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘
      │                │                  │                │              │              │
      │ 1. event arrives (from UoW commit)│                │              │              │
      │                │                  │                │              │              │
      │ 2. saga_mgr.handle(event)         │                │              │              │
      ├───────────────►│                  │                │              │              │
      │                │                  │                │              │              │
      │                │ 3. registry.get_sagas_for_event() │              │              │
      │                ├─────────────────►│                │              │              │
      │                │                  │                │              │              │
      │                │ 4. saga_classes[] │                │              │              │
      │                │◄─────────────────┤                │              │              │
      │                │                  │                │              │              │
      │                │ 5. For each saga_class:           │              │              │
      │                │                  │                │              │              │
      │                │ 6. repo.find_by_correlation_id()  │              │              │
      │                ├──────────────────────────────────►│              │              │
      │                │                  │                │              │              │
      │                │ 7. state (existing or new)        │              │              │
      │                │◄──────────────────────────────────┤              │              │
      │                │                  │                │              │              │
      │                │ 8. saga = saga_class(state)       │              │              │
      │                │                  │                │              │              │
      │                │ 9. saga.handle(event)             │              │              │
      │                ├─────────────────────────────────────────────────►│              │
      │                │                  │                │              │              │
      │                │                  │                │              │ ┌────────────┴─┐
      │                │                  │                │              │ │ 9a. Skip if  │
      │                │                  │                │              │ │ terminal or  │
      │                │                  │                │              │ │ duplicate    │
      │                │                  │                │              │ │ 9b. _handle_ │
      │                │                  │                │              │ │   event() →  │
      │                │                  │                │              │ │   registered │
      │                │                  │                │              │ │   handler    │
      │                │                  │                │              │ │ 9c. dispatch │
      │                │                  │                │              │ │   commands   │
      │                │                  │                │              │ │ 9d. push     │
      │                │                  │                │              │ │   compensa-  │
      │                │                  │                │              │ │   tions      │
      │                │                  │                │              │ └──────────────┘
      │                │                  │                │              │
      │                │ 10. commands = saga.collect_commands()           │              │
      │                │◄────────────────────────────────────────────────┤              │
      │                │                  │                │              │
      │                │ 11. _trace_commands (propagate correlation/causation IDs)      │
      │                │                  │                │              │
      │                │ 12. Save pending commands (recovery checkpoint) │              │
      │                ├──────────────────────────────────►│              │              │
      │                │                  │                │              │
      │                │ 13. Dispatch commands one-by-one  │              │              │
      │                │     (save state after each)       │              │              │
      │                ├─────────────────────────────────────────────────────────────────►│
      │                │                  │                │              │              │
      │                │ 14. Clear pending, save final state│              │              │
      │                ├──────────────────────────────────►│              │              │
      │                │                  │                │              │
```

### Compensation path (on handler failure)

```
┌──────────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ SagaManager  │   │   Saga   │   │ SagaRepo │   │CommandBus│
└──────┬───────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘
       │                │              │              │
       │ saga.handle(event) raises     │              │
       │                │              │              │
       │ 1. saga.fail(reason, compensate=True)        │              │
       ├───────────────►│              │              │
       │                │              │              │
       │                │ 2. execute_compensations()   │              │
       │                │    (pops from compensation_  │              │
       │                │     stack in LIFO order)     │              │
       │                │              │              │
       │ 3. _dispatch_compensations(state, cmds)      │              │
       │◄───────────────┤              │              │
       │                │              │              │
       │ 4. For each compensation command (LIFO):     │              │
       │    trace → dispatch via CommandBus            │              │
       ├──────────────────────────────────────────────────────────────►│
       │                │              │              │
       │    On success: │              │              │
       │    state.status = COMPENSATED│              │              │
       │    On failure: │              │              │
       │    record in failed_compensations             │              │
       │    state.status = FAILED      │              │              │
       │                │              │              │
       │ 5. Save state  │              │              │
       ├──────────────────────────────►│              │
       │                │              │              │
```

### Walkthrough

| Step | Component | Action | Key code |
|------|-----------|--------|----------|
| 1 | MessageBus | Event dispatched from `_dispatch_events()` after UoW commit | `message_bus.py` |
| 2 | MessageBus | `SagaManager.handle(event)` called (registered as event handler via `bind_to()`) | `saga/manager.py` |
| 3 | SagaManager | Looks up saga classes registered for this event type via `SagaRegistry` | `registry.get_sagas_for_event()` |
| 5 | SagaManager | Iterates over each matching saga class | `_process_saga()` |
| 6 | SagaManager | Loads existing `SagaState` by `correlation_id`, or creates initial state | `repository.find_by_correlation_id()` |
| 7 | SagaManager | If terminal state → skip; if suspended → resume | State machine guard |
| 8 | SagaManager | Instantiates saga with loaded state | `saga_class(state)` |
| 9 | Saga | `saga.handle(event)` — idempotent (skips already-processed events) | `saga/saga.py` |
| 9a | Saga | Skips if terminal or duplicate event | `is_terminal`, `is_event_processed()` |
| 9b | Saga | Dispatches to `_when_{EventType}` handler or user-registered handler | `_handle_event()` |
| 9c | Saga | Handler queues commands via `self.dispatch(cmd)` | `dispatch()` |
| 9d | Saga | If compensation declared via `on()`, pushes onto `compensation_stack` | `add_compensation()` |
| 10 | SagaManager | Collects queued commands from saga | `saga.collect_commands()` |
| 11 | SagaManager | Propagates tracing IDs onto commands: `correlation_id` from state, `causation_id` = `state.id` | `_trace_command()` |
| 12 | SagaManager | Serializes and saves pending commands as recovery checkpoint | `repository.save(state)` |
| 13 | SagaManager | Dispatches commands **one-by-one**, marking each as dispatched and saving after each | `_dispatch_and_persist_commands()` |
| 14 | SagaManager | Clears `pending_commands`, saves final state | `state.pending_commands.clear()` |

### Saga state machine

```
                  ┌─────────────┐
                  │   PENDING   │  ← Initial state
                  └──────┬──────┘
                         │ first event handled
                         ▼
                  ┌─────────────┐
            ┌────►│   RUNNING   │◄───┐
            │     └──────┬──────┘    │
            │            │           │
            │   suspend()│           │ resume()
            │            ▼           │
            │     ┌─────────────┐    │
            │     │  SUSPENDED  │────┘
            │     └─────────────┘
            │
            │            │ fail()
            │            ▼
            │     ┌──────────────┐
            │     │ COMPENSATING │  ← executing compensations (LIFO)
            │     └──────┬───────┘
            │            │
            │     ┌──────┴──────┐
            │     ▼             ▼
            │  ┌───────────┐ ┌────────┐
            │  │COMPENSATED│ │ FAILED │  ← compensation errors
            │  └───────────┘ └────────┘
            │
            │            │ complete()
            │            ▼
            │     ┌───────────┐
            └─────│ COMPLETED │  ← normal end
                  └───────────┘
```

---

## 6.5 Subscription Catch-Up — Projections from Global Log

How the `SubscriptionRunner` polls the global event log, filters events by type, delivers them to projections, and tracks progress via checkpoints.

### Sequence diagram

```
┌───────────────────┐   ┌──────────────┐   ┌────────────────┐   ┌────────────────────┐   ┌───────────────┐
│ SubscriptionRunner │   │ Checkpoint   │   │  EventStore    │   │ Subscription       │   │  Projection   │
│ run() / run_once() │   │ Store        │   │                │   │ (id + event_types) │   │               │
└─────────┬──────────┘   └──────┬───────┘   └───────┬────────┘   └──────────┬─────────┘   └───────┬───────┘
          │                     │                    │                       │                      │
          │  ┌────────────────────────────────────────────────────┐         │                      │
          │  │ Polling loop (until stop() called)                 │         │                      │
          │  └────────────────────────────────────────────────────┘         │                      │
          │                     │                    │                       │                      │
          │ 1. _process_cycle() │                    │                       │                      │
          │                     │                    │                       │                      │
          │ 2. Load checkpoints │                    │                       │                      │
          │     for each sub    │                    │                       │                      │
          ├────────────────────►│                    │                       │                      │
          │                     │                    │                       │                      │
          │ 3. min_checkpoint = │                    │                       │                      │
          │     min(all)        │                    │                       │                      │
          │                     │                    │                       │                      │
          │ 4. stream = read_all(from_version=min)   │                       │                      │
          ├─────────────────────────────────────────►│                       │                      │
          │                     │                    │                       │                      │
          │ 5. For each subscription:                │                       │                      │
          │     (shared stream, independent dispatch) │                      │                      │
          │                     │                    │                       │                      │
          │ 6. Slice stream from sub's checkpoint    │                       │                      │
          │                     │                    │                       │                      │
          │ 7. Filter by isinstance(event, sub.event_types)                 │                      │
          │                     │                    │                       │                      │
          │ 8. process_batch(matching_events, sub)   │                       │                      │
          ├─────────────────────────────────────────────────────────────────►│                      │
          │                     │                    │                       │                      │
          │                     │                    │                       │ 9. projection.handle(event)
          │                     │                    │                       ├─────────────────────►│
          │                     │                    │                       │                      │
          │                     │                    │                       │    ┌─────────────────┴┐
          │                     │                    │                       │    │ _when_{EventType} │
          │                     │                    │                       │    │ checkpoint++      │
          │                     │                    │                       │    └──────────────────┘
          │                     │                    │                       │                      │
          │ 10. Save checkpoint │                    │                       │                      │
          │     (on success)    │                    │                       │                      │
          ├────────────────────►│                    │                       │                      │
          │                     │                    │                       │                      │
          │                     │                    │                       │                      │
          │ 10'. On failure:    │                    │                       │                      │
          │      log warning    │                    │                       │                      │
          │      backoff sleep  │                    │                       │                      │
          │      do NOT save    │                    │                       │                      │
          │      checkpoint     │                    │                       │                      │
          │      (will retry)   │                    │                       │                      │
          │                     │                    │                       │                      │
          │ 11. No events?      │                    │                       │                      │
          │      sleep(poll_    │                    │                       │                      │
          │      interval)      │                    │                       │                      │
          │                     │                    │                       │                      │
```

### Walkthrough

| Step | Component | Action | Key code |
|------|-----------|--------|----------|
| 1 | `SubscriptionRunner` | Polling loop calls `_process_cycle()` | `infrastructure/subscription.py` |
| 2 | Runner | Loads checkpoint for each registered subscription | `checkpoint_store.load(sub_id)` |
| 3 | Runner | Computes `min_checkpoint` from all subscriptions | `min(checkpoints.values())` |
| 4 | Runner | **Single DB read** from global event log starting at `min_checkpoint` | `event_store.read_all(from_version=min)` |
| 5 | Runner | Iterates each subscription independently from the shared stream | `_dispatch_to_subscription()` |
| 6 | Runner | Slices `stream.events[offset:]` from subscription's checkpoint | `offset = sub_checkpoint - stream_start` |
| 7 | Runner | Filters events by `isinstance(event, sub.event_types)` | List comprehension |
| 8 | Runner | Calls abstract `process_batch(matching, subscription)` | Subclass implementation |
| 9 | Projection | `projection.handle(event)` dispatches to `_when_{EventType}` method by convention | `es/projection.py` |
| 10 | Runner | On success: saves checkpoint to `CheckpointStore` | `checkpoint_store.save(sub_id, stream.version)` |
| 10' | Runner | On failure: logs warning, sleeps `failure_backoff_seconds`, **does not save checkpoint** → at-least-once guarantee | Exception handler in `_dispatch_to_subscription()` |
| 11 | Runner | If no new events in global log: sleeps `poll_interval_seconds` before next cycle | `run()` |

### Key properties

| Property | Implementation |
|----------|---------------|
| **At-least-once** | Checkpoint only updated after successful `process_batch()`. Failed batches are retried on next poll. |
| **Single DB read** | All subscriptions share one `read_all()` call. Each subscription slices and filters independently. |
| **Independent progress** | Each subscription tracks its own checkpoint. A slow subscription does not block others. |
| **Failure isolation** | One subscription's failure does not prevent others from advancing. Backoff + retry for the failing one. |
| **Graceful stop** | `stop()` sets a flag; current batch completes before the polling loop exits. |

---

## Cross-Scenario Reference

The table below maps each runtime scenario to the building blocks (§5) involved:

| Scenario | Primary building blocks | Supporting blocks |
|----------|------------------------|-------------------|
| **Command handling** | `MessageBus`, `CommandBus`, `UnitOfWork`, `MessagePipeline` | `AggregateRoot`, `Repository`, `DomainEvent`, pipeline behaviors |
| **Query handling** | `MessageBus`, `QueryBus`, `MessagePipeline` | `Query`, `QueryResult` |
| **Event replay** | `EventSourcedRepository`, `EventSourcedAggregateRoot`, `EventStore` | `SnapshotStore`, `UpcasterRegistry`, `EventRegistry` |
| **Saga orchestration** | `SagaManager`, `Saga`, `SagaState`, `SagaRegistry` | `SagaRepository`, `CommandBus`, `DomainEvent` |
| **Subscription catch-up** | `SubscriptionRunner`, `Subscription`, `CheckpointStore` | `EventStore`, `EventSourcedProjection` |
