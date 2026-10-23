# How to Upgrade to the Next Release

> **Prerequisites:** none. **Release:** next after 0.3.1.

This page collects the behaviour changes merged since 0.3.1. Read it before upgrading; each entry says who
is affected and what to do. See [Writing an entry](_index.md#writing-an-entry) for the format. The changes
in 0.3.1 itself are in [How to Upgrade to 0.3.1](upgrade-to-0.3.1.md).

## Breaking

_None._ Breaking changes ship only through `release/v*` branches.

## Behaviour changes

The first four entries come from one change: in a command pipeline, the commit now runs in the bus's
innermost step, right after the handler and inside every behavior
([ADR-079](../../../adr/ADR-079-commit-inside-the-command-pipeline.md)). Before, the bus committed after
the whole pipeline had returned.

### Locks are held through the commit (DCE-143)

**What changed:** `AggregateLockingBehavior` releases its keys after `next()` returns, as before, but `next()`
now includes the commit. The keys are therefore held until the command has committed, and a second command
on the same key reads what the first one committed. Before, the keys were released before the commit, and
two locked read-modify-write commands on one key could lose an update. A nested command commits and
releases its own keys before the outer command commits and releases its keys. The rollback and the unit of
work's exit still run after the release, so a repository shared by concurrent units of work is still unsafe.

**Who is affected:** `LockProvider` adapters with a lease or TTL; applications with slow commits on
contended keys, which now wait longer for the lock.

**What to do:** make the lease or TTL of your lock provider cover the commit as well as the handler. A
failed release after a successful command is still a WARNING; the commit has already happened.

### Idempotency records the result after the commit (DCE-143)

**What changed:** `IdempotencyBehavior` calls `store.set()` after `next()` returns, which is now after the
commit. A command whose commit fails, at `_flush()`, `_write_outbox()` or `_commit()`, is no longer cached:
a retry with the same `command_id` runs the handler again. Before, the result was stored before the commit,
so the retry got the cached result back although nothing was persisted. A `store.set()` that raises after
the commit is now a WARNING on `pydomain.pipeline` that names the error's type only
(`Idempotency record failed for PlaceOrder: ConnectionError`), and `dispatch()` returns the result; before,
the dispatch failed with `CommandExecutionError`. Recording is at-least-once: a crash between the commit
and the record, a failed record, or a `_commit()` that raises after the database committed lets a retry
run the handler again.

**Who is affected:** applications that rely on a retry after a failed commit returning the cached result;
tests that expect a failing `store.set()` to fail the dispatch.

**What to do:** nothing for most applications: a retry after a failed commit now does the work. Make
handlers safe to repeat where at-least-once matters (a unique constraint, an optimistic-concurrency
check). To test the failure path, `FakeUnitOfWork(fail_at="commit")` (or `"flush"`, `"write_outbox"`) makes
the next commit fail once; see [Use a Fake Unit of Work](../testing/use-fake-uow.md#7-make-a-commit-fail).

### A behavior's code after `next()` runs after the commit (DCE-143)

**What changed:** in a command pipeline, the code a behavior runs after `await next()` now runs after the
unit of work committed, and a commit that fails raises out of `next()`, so the behavior's error path sees it;
`LoggingBehavior` logs a failed commit as `Failed COMMAND …: <Type>` instead of `Completed COMMAND …`. Writes a
behavior makes through `ctx.uow` after `next()` are no longer committed. A pipeline that never reaches the
handler, such as a cached duplicate or a behavior that returns without calling `next()`, now commits
nothing: its unit of work rolls back when the bus leaves it, and `dispatch()` returns no events. Before,
the bus committed after every pipeline that returned. Query and event pipelines are unchanged; logs still
name the registered handler. A behavior that calls `next()` twice in a command pipeline now fails the
dispatch before the handler runs again, with `CommandExecutionError` whose cause is `PipelineReentryError`
(see [A second `next()` in a command pipeline is refused (DCE-144)](#a-second-next-in-a-command-pipeline-is-refused-dce-144)).

**Who is affected:** custom behaviors that write through `ctx.uow` after `next()`; behaviors that stage
work and then short-circuit, expecting it to be committed; tests that assert a commit after a
short-circuiting behavior (for example a recording unit of work's hooks).

**What to do:** move writes from after `next()` into the handler, or before `next()`. A behavior that
short-circuits commits nothing, so work that must persist belongs in the handler. Update tests that expect a short-circuiting pipeline to commit: it now rolls back.

```python
# Before: committed, because the bus committed after the pipeline
async def handle(self, ctx, next):
    result = await next()
    await ctx.uow.audit.save(AuditEntry(...))  # no longer committed
    return result

# Now: write in the handler, which runs before the commit
async def __call__(self, cmd, uow):
    ...
    await uow.audit.save(AuditEntry(...))
```

### Behaviors see the committed events in `ctx.new_events` (DCE-143)

**What changed:** the terminal step sets `ctx.new_events` to the committed, stamped events right after the
commit, so a behavior can read them after `next()` returns. Before, `ctx.new_events` was set only after the
pipeline had finished, and every behavior saw an empty list. `dispatch()` returns a copy of the same
events, so a behavior that changes `ctx.new_events` does not change what is returned.
`ctx.new_events` stays empty before `next()` and when the handler was not reached; an empty list does not
say whether a commit happened, because a command can commit without raising events.

**Who is affected:** behaviors that read `ctx.new_events`, for example to count or trace events.

**What to do:** nothing; read `ctx.new_events` after `next()` returns.

### A second `next()` in a command pipeline is refused (DCE-144)

**What changed:** a behavior that calls `next()` a second time in a command pipeline now gets a new
`PipelineReentryError` (a `CQRSError`, exported from `pydomain.cqrs`) before the handler runs again. The
bus wraps it in `CommandExecutionError` like every pipeline failure, so `dispatch()` raises
`CommandExecutionError` whose `__cause__` is the `PipelineReentryError`. A first run that already committed
keeps its commit, and the scope is not rolled back. Before, the second call ran the handler again: after a
failed first run, a retry behavior committed the events of both runs, and after a successful first run the
second commit failed with `UnitOfWorkAlreadyCommittedError`. Query and event pipelines are unchanged: their
`next()` can still be called any number of times.

**Who is affected:** custom behaviors that call `next()` more than once in a command pipeline, such as a
retry behavior, and tests that expect the handler to run again.

**What to do:** remove the second call. To retry a failed command, dispatch it again (a new dispatch opens a
new unit-of-work scope), or retry in the caller, around `dispatch()`.

```python
# Before: the retry ran the handler a second time inside one dispatch
async def handle(self, ctx, next):
    try:
        return await next()
    except TransientError:
        return await next()

# Now: let the dispatch fail and retry the dispatch
async def retry_dispatch(bus, command):
    try:
        return await bus.dispatch(command)
    except CommandExecutionError as error:
        if not isinstance(error.__cause__, TransientError):
            raise
        return await bus.dispatch(command)
```

### A cancellation waits for the storage commit and rollback (DCE-145)

**What changed:** `AbstractUnitOfWork` now runs its `_commit()` and `_rollback()` hooks to completion when
the calling task is cancelled while they run, and the cancellation propagates once the hook ended
([ADR-079](../../../adr/ADR-079-commit-inside-the-command-pipeline.md)). Before, a cancellation interrupted
them: a second cancellation cut a rollback short, and a cancellation that arrived after the storage commit
took effect still rolled the scope back, cleared its events and looked like a failure.

- A cancellation during `_commit()`, or one requested earlier and not yet delivered, lets the commit finish;
  the scope is committed, no rollback runs, and the caller gets the `CancelledError`. Under an anyio cancel
  scope the cancellation arrives at the next checkpoint instead, so a dispatch with none left returns
  normally.
- `IdempotencyBehavior` records a command whose commit completed even when its dispatch then raises (a
  cancellation during the commit, the record or a lock release, a handler that called `commit()` itself,
  or a behavior that calls `next()` a second time), so a retry returns the cached result instead of running the handler again. A record that fails
  there is a note on the error, `clean-up also failed: <Type>`.
- The events of such a command are not returned, so `MessageBus` does not hand them to in-process event
  handlers; they reach consumers through the outbox.
- `_flush()` and `_write_outbox()` still stop at a cancellation, and the scope rolls back.
- A hook that raises `CancelledError` itself, while nothing cancels the caller, is now that hook's failure:
  the bus raises `CommandExecutionError` whose `__cause__` is a `CQRSError` raised from the
  `CancelledError`, and a direct `commit()` or `rollback()` raises that `CQRSError`. Before, the
  `CancelledError` reached the caller as if it had been cancelled. With `shield_hooks = False` (below) it
  still does.
- Under asyncio, anyio's asyncio backend included (FastAPI, Starlette), the two hooks run in a task of
  their own, in the caller's context: context variables are visible both ways, but
  `asyncio.current_task()` inside a hook is not the dispatching task.
- The shield adds about 20 µs to a dispatch, and about 35 µs with `IdempotencyBehavior` (Python 3.12,
  in-memory fakes): negligible next to a database round trip.

**Who is affected:** units of work whose storage is keyed by the current task, such as SQLAlchemy's
`async_scoped_session(scopefunc=asyncio.current_task)`, in FastAPI and Starlette applications too: in the
hook task they see another session, and the commit silently loses the writes. Code that relied on a cancellation interrupting a slow commit or
rollback: a cancelled dispatch now outlives its cancellation by that long. Tests that expect a `_rollback()`
raising `CancelledError` to propagate as a cancellation. `ProcessedMessageStore` adapters built on the unit
of work's task-scoped session: the record after a commit runs in its own task too.

**What to do:** a unit of work with task-keyed storage sets the new class attribute `shield_hooks` to
`False`; its hooks then run inline in the calling task, cancellable, as before:

```python
class ScopedSessionUnitOfWork(AbstractUnitOfWork):
    shield_hooks = False
```

A session opened per unit of work (in the factory or in `__aenter__`) needs nothing. Build idempotency
stores on a connection pool that works from any task. Update tests that expect a hook's own
`CancelledError`: catch `CommandExecutionError` (or `CQRSError` for direct use) and read its `__cause__`.
A unit of work that implements only the `UnitOfWork` Protocol is not shielded; shield its storage commit
and rollback itself.

### `anyio>=4` is a runtime dependency (DCE-145)

**What changed:** `pydomain-lib` now requires `anyio>=4`, for the shield above.
`pydomain.cqrs.unit_of_work` is the only runtime module that imports it, when a hook or the post-commit
record runs, so `import pydomain` still loads only `pydantic` and `uuid-utils`. The test suite passes at
the declared floor, `anyio==4.0`. The `testing` extra no longer lists `anyio`, and installs `pytest` only.

**Who is affected:** projects that pin `anyio` below 4, or that install without dependencies.

**What to do:** allow `anyio>=4`. Most projects already have it: FastAPI, Starlette and httpx depend on it.

### A concurrent duplicate gets `CommandInProgressError` (DCE-146)

**What changed:** a new optional Protocol, `MessageReservationStore` (`reserve`, `release`), lets
`IdempotencyBehavior` reserve a command before its handler runs
([ADR-079](../../../adr/ADR-079-commit-inside-the-command-pipeline.md) points 4 and 5). When the behavior's
store implements it, a dispatch of a `command_id` that another dispatch is still running raises the new
`CommandInProgressError` (a `CQRSError`, exported from `pydomain.cqrs`) before its handler runs, wrapped
in `CommandExecutionError` like every pipeline failure; nothing of it commits. Before, two concurrent
dispatches of one `command_id` both ran the handler, with or without lock keys.
`FakeProcessedMessageStore` implements the Protocol, so pipelines that use it reserve now.

- A command that fails or is cancelled before its commit releases its reservation, so its retry runs.
- A committed command completes its reservation with `set`; a retry gets the cached result.
- A reservation that is not released (a crash, a `release` that raised, a `set` after the commit that
  raised, a dispatch closed while suspended) blocks its id until the store's TTL frees it; until then,
  retries get `CommandInProgressError`. A failed `release` is a note on the error,
  `clean-up also failed: <Type>`.
- `reserve` and `release` run protected from cancellation, like the commit, in a task of their own under
  asyncio, so the store must work from any task.
- A store that implements only `ProcessedMessageStore` keeps the old flow, where concurrent duplicates can
  both run.

**Who is affected:** applications and tests that dispatch one command concurrently with
`FakeProcessedMessageStore`, which now see `CommandInProgressError` for the duplicate; idempotency stores
that want concurrent duplicates refused; tests that pin the store calls, which now include `reserve`.
A store class that already has methods named `reserve` and `release` becomes a reserving store: the
behavior checks `isinstance(store, MessageReservationStore)` (a runtime-checkable Protocol, which looks at
the method names only) once, when it is built, and then calls them as the Protocol describes.

**What to do:** treat `CommandExecutionError` whose `__cause__` is `CommandInProgressError` as "retry
later". To refuse concurrent duplicates with your own store, implement `reserve` and `release` with a
token and a TTL longer than a command's handler and commit (see the
[Redis example](../cqrs/add-idempotency.md#production-store-redis-example)), and run
`MessageReservationStoreContract` against it
([Run a Contract Suite](../testing/run-contract-suites.md)). `FakeProcessedMessageStore(reservation_ttl=...)`
makes the fake's reservations expire, to test a crash.

### `check_and_set` marks are not results (DCE-148)

**What changed:** `FakeProcessedMessageStore` keeps the marks of `check_and_set` in a set of their own
([ADR-080](../../../adr/ADR-080-identify-delivered-events-for-event-idempotency.md)). `check_and_set(id)`
reads marks only and creates no result: `get(id)` stays `MISSING` afterwards, and `check_and_set(id)` after
`set(id, result)` now returns `False`, where it returned `True`. `contains(id)` is still true for a result or
a mark. `ProcessedMessageStoreContract` has a new case, `test_check_and_set_creates_no_result`. The Protocol
is unchanged. Before, the fake stored a mark as the result `True`, so a command id and an event key in one
store collided.

**Who is affected:** authors of a `ProcessedMessageStore` adapter that run the contract suite (the new case
fails for a store that turns a mark into a result); tests that expect `check_and_set` to return `True` after
`set()` for the same id.

**What to do:** store the marks of `check_and_set` apart from the results `get` reads, as the
[Add Idempotency](../cqrs/add-idempotency.md) examples do, and run `ProcessedMessageStoreContract`
([Run a Contract Suite](../testing/run-contract-suites.md)). Update a test that calls `check_and_set` after
`set()` for one id.

### An outbox store refuses a duplicate `message_id` (DCE-148)

**What changed:** `OutboxStore.append` raises `CQRSError` for an entry whose `message_id` is already stored,
or repeats within the batch, and stores nothing of the batch
([ADR-080](../../../adr/ADR-080-identify-delivered-events-for-event-idempotency.md)). `FakeOutboxStore`
does so, and `OutboxStoreContract` has two new cases, `test_append_refuses_a_duplicate_message_id` and
`test_append_refuses_a_batch_that_repeats_a_message_id`. Before, the fake appended the duplicate. The method
signatures are unchanged.

**Who is affected:** authors of an `OutboxStore` adapter that run the contract suite (the new cases fail for
a store that accepts a duplicate or stores part of a batch); tests that append the same `message_id` twice to a `FakeOutboxStore`.

**What to do:** reject a duplicate `message_id` in your adapter, for example with a unique constraint, and
raise `CQRSError` for it (an SQL adapter inside the caller's transaction needs a SAVEPOINT or a pre-check, because the database aborts the transaction on the violation); run `OutboxStoreContract`
([Run a Contract Suite](../testing/run-contract-suites.md)). Give each outbox entry a distinct `message_id`
in your tests.

### Inbound event ids are derived from the integration event (DCE-149)

**What changed:** the `InboundEventGateway` gives a translated domain event whose `event_id` the translator left
out the id `uuid5(namespace, integration_event.event_id)`, with the fixed namespace
`uuid5(NAMESPACE_URL, "https://github.com/mgourlis/pydomain/inbound-event-id")`
(`52a1e11d-5cfe-52a6-971b-a1b2961dd04f`)
([ADR-080](../../../adr/ADR-080-identify-delivered-events-for-event-idempotency.md) point 1). Every redelivery
of one integration event now has one domain `event_id`, with or without a `correlation_id`; before, each
delivery got a new one. The topic is not part of the id. An id the translator sets, in the constructor, through
a `mode="before"` validator or a field validator of the event class or with
`model_copy(update={"event_id": ...})`, is kept, and so is the id of an event built with `model_construct()`.
A subclass's `mode="after"` or `mode="wrap"` model validator that replaces the id is not seen: the gateway
derives the id over it. `causation_id` is unchanged: the integration event's
`event_id`, stamped only when the payload has a `correlation_id`.

`DomainEvent` now records, in a private attribute, whether its validation generated the `event_id`.
`model_dump()`, `model_fields_set`, equality and hashing do not see it. Recording it costs about 2 µs per event
construction or validation (2.7 → 4.7 µs), and `==` between events goes from 0.45 to 1.87 µs (a
micro-benchmark on Python 3.12, B-173).

An event class validated by alias only (an `alias_generator` without `populate_by_name`) now gets its generated
`event_id` from its own id generator, under the alias; before, the id came from the base class's generator
(B-170, [ADR-070](../../../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md)).

**Who is affected:** consumers whose handlers, stores or tests read the `event_id` of an inbound domain event,
which is now a UUIDv5 instead of a freshly generated id (UUIDv7 by default), so it no longer orders by time; tests that expect two deliveries
of one message to give two ids; translators that build events with `model_construct()`, which keep a fresh id.

**What to do:** nothing, to get one id per integration event. To keep an id of your own, set `event_id` in the
translator. Do not sort inbound events by `event_id`; use `occurred_at`. Build translated events with the
constructor or `model_validate()`, not `model_construct()`.

### A payload without `event_id` logs a WARNING (DCE-149)

**What changed:** when a raw payload has no `event_id` key, the `InboundEventGateway` logs, at WARNING, a line
that names the integration class and the topic only, and dispatches the translated event unchanged
([ADR-080](../../../adr/ADR-080-identify-delivered-events-for-event-idempotency.md) point 2):

```text
WARNING No event_id in the payload of ShipmentFailedIntegrationEvent on topic 'shipping.shipment.failed' — dispatching without deduplication
```

`IntegrationEvent` fills the missing id with a new one on every delivery, so there is nothing stable to derive
from, and redeliveries of such a payload are not recognised as one event. pydomain's outbox always writes
`event_id`, so only foreign producers send such payloads. Before, they passed silently.

**Who is affected:** consumers of producers that omit `event_id`; tests that assert no WARNING on the
`pydomain.message_subscriber` logger for such payloads.

**What to do:** ask the producer to send a stable `event_id`. Until then, a handler that must not run twice
deduplicates on a business key of its own.
