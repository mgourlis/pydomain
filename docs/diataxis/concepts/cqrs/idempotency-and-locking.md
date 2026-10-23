# Idempotency & Locking

<!-- docs-test: runnable -->
<!-- docs-test: setup
from collections.abc import Hashable
from typing import Any, Protocol
from uuid import UUID

from pydomain.cqrs import Command, EmptyCommandResult


class PlaceOrder(Command[EmptyCommandResult]):
    order_id: UUID


class CancelOrder(Command[EmptyCommandResult]):
    order_id: UUID
-->

> **Adoption Level:** 3 — CQRS with At-Least-Once Delivery
> **Module:** `pydomain.cqrs.idempotency`, `pydomain.cqrs.locking`

## Overview

Two complementary mechanisms protect against duplicate processing and concurrent modification in distributed systems:

| Mechanism | Protects Against | Module |
|-----------|-----------------|--------|
| **Idempotency** | Duplicate commands and events (at-least-once delivery) | `idempotency` |
| **Distributed Locking** | Concurrent modification of the same aggregate | `locking` |

They work together: idempotency checks happen before locking, so duplicate commands skip lock acquisition entirely.

## Idempotency

### The Problem

In distributed systems, messages can be delivered more than once. Without idempotency, processing the same `PlaceOrder` command twice creates two orders.

### The Solution

The `IdempotencyBehavior` tracks which command IDs have been processed and caches their results. When a duplicate arrives, the cached result is returned immediately — the handler is never called.

### `ProcessedMessageStore`

```python
from pydomain.cqrs.idempotency import ProcessedMessageStore, MISSING


class ProcessedMessageStore(Protocol):
    async def get(self, message_id: UUID) -> Any:
        """Return the cached result, or MISSING."""
        ...

    async def set(self, message_id: UUID, result: Any) -> None:
        """Persist result for message_id."""
        ...

    async def contains(self, message_id: UUID) -> bool:
        """Return True if message_id has been processed."""
        ...

    async def check_and_set(self, message_id: UUID) -> bool:
        """Atomically mark message_id; True if it was already processed."""
        ...
```

The `MISSING` sentinel distinguishes "never processed" from a cached `None` result.

### `MessageReservationStore`

A store can also implement the optional `MessageReservationStore` Protocol, from the same module. With it, the behavior **reserves** the command before the handler runs, so one `command_id` runs at most once at a time:

```python
from collections.abc import Hashable

from pydomain.cqrs.idempotency import MessageReservationStore


class MessageReservationStore(Protocol):
    async def reserve(self, message_id: UUID) -> Hashable | None:
        """Atomically reserve message_id; return a token, or None if it is already reserved."""
        ...

    async def release(self, message_id: UUID, token: Hashable) -> None:
        """Drop the reservation token took, if it still holds message_id; never another one."""
        ...
```

What every reserving store promises (`MessageReservationStoreContract` checks it):

- `reserve` is atomic: of concurrent calls for one id, one gets a token, the others `None`.
- `release(message_id, token)` frees only the reservation that `token` took. A late release, after a TTL freed the id and another dispatch reserved it, frees nothing.
- `set(message_id, result)` completes the reservation; `get` then returns the result. An id that is only reserved is not a result: `get` returns `MISSING`.
- Once completed, `reserve` returns `None`, at least until the TTL, also after a `release` with the old token: a duplicate whose first `get` missed just before the completion finds the result instead of running again.
- Reservations have a key space of their own, separate from results and from the marks of `check_and_set`: reserving an id never marks it processed, and `check_and_set` never reserves it.
- A reservation that is neither released nor completed (its process crashed, or its `release` failed) blocks the id until the store's TTL frees it. Give reservations a TTL longer than a command's handler and commit; nothing in pydomain enforces it.

`FakeProcessedMessageStore` implements both Protocols. A store that implements only `ProcessedMessageStore` keeps working, without the reservation.

### How It Works

```
1. Command arrives with command_id=abc-123
2. IdempotencyBehavior checks store.get("abc-123")
3. If cached → return cached result (handler skipped, nothing committed)
4. With a reserving store → store.reserve("abc-123")
   - no token: get again; the cached result if the first dispatch completed
     meanwhile, otherwise CommandInProgressError (handler skipped)
5. next(): the handler runs and the unit of work commits
6. After the commit → store.set("abc-123", result), which completes the reservation
   If next() raised before the commit → store.release("abc-123", token)
```

A concurrent duplicate gets `CommandInProgressError` (a `CQRSError`), raised before its handler runs, so nothing of it is committed. The caller of `dispatch()` gets it as the `__cause__` of `CommandExecutionError`; its message names the command type only. Retry later: once the first dispatch completed, the retry returns its cached result; if the first failed, the retry runs the handler. This holds with and without lock keys. Without a reserving store, two concurrent dispatches of one `command_id` can both run the handler.

A command that fails or is cancelled before its commit releases its reservation, so its retry runs. The release comes before the unit of work's rollback, which runs at the scope's exit, so a retry can start while the first dispatch still rolls back: harmless with a database, but a repository shared in memory between dispatches is unsafe anyway (see Distributed Locking below). A `release` that raises becomes a note on the error, `clean-up also failed: <type>`, and the id stays reserved until the store's TTL: retries get `CommandInProgressError` until then. A dispatch closed while suspended (`GeneratorExit`) cannot await the store, so its reservation also stays until the TTL.

`reserve`, `release` and the record after a commit run protected from cancellation, as the commit does. If the caller is cancelled while `reserve` runs, `reserve` completes, the behavior releases the new token at once, and the cancellation propagates, so no reservation leaks.

The result is recorded only after the commit succeeded, so a command whose commit failed is not cached: a retry with the same `command_id` runs the handler again. If `store.set()` itself fails after the commit, the behavior logs a WARNING on `pydomain.pipeline` with the error's type only (`Idempotency record failed for PlaceOrder: ConnectionError`) and still returns the result; a retry then runs the handler again. With a reserving store the reservation is kept, not released, so an immediate duplicate is still refused with `CommandInProgressError`; once the TTL frees the id, a retry runs the handler again.

A command whose commit completed (with an `AbstractUnitOfWork`; a Protocol-only unit of work that raises from `commit()` counts as not committed) is recorded even when its dispatch then raises: a cancellation that arrived during the commit (the commit finishes first), during the record itself, or during a lock release after the commit, a handler that called `commit()` itself, and a behavior that calls `next()` a second time. The behavior records the result, then re-raises the error, so a retry returns the cached result instead of running the committed work again. The record after a commit, `reserve` and `release` are protected from cancellation the same way as the commit, so the store must work from any task: use a connection pool (`redis.asyncio`, an asyncpg pool, an `AsyncEngine` with a short session per call), never the unit of work's task-scoped session. A record that fails there becomes a note on the re-raised error, `clean-up also failed: <type>`, not a WARNING.

Such a command does not return its events, so `MessageBus` never hands them to in-process event handlers, and the retry returns the cached result without them. Projections that must see every committed change read them through the outbox path (the outbound relay, the inbound gateway, or a catch-up subscription), which also survives a crash; in-process handlers stay best-effort.

This is **at-least-once**, not exactly-once. A retry runs the handler again, although the work was committed, when no record was written:

- the process crashed between the commit and the record;
- `_commit()` raised after the storage commit took effect (an ambiguous commit failure, such as a lost connection after the database committed): the unit of work rolls back, the caller sees a failure, and the retry applies the work a second time;
- a handler that called `commit()` itself and then raised, so there is no result to record;
- the dispatch was closed while suspended, as when a pending task is garbage-collected: a closing coroutine cannot wait for the store.

Make the handler safe to repeat where that matters, for example with a unique constraint or an optimistic-concurrency check. Recording the result inside the same transaction as the work is planned for a later release.

### Event Idempotency

`EventIdempotencyBehavior` does for an event handler what `IdempotencyBehavior` does for a command, with the same store. An event handler returns nothing, so the behavior records a mark, not a result.

**The key.** Each handler names itself with `consumer`, and the key is

```
uuid5(uuid5(EVENT_KEY_NAMESPACE, consumer), str(event.event_id))
```

where `EVENT_KEY_NAMESPACE` is the fixed value `6122d529-5666-59f9-aeb0-cda93ddbb124`. Keys are `UUID`s derived under pydomain's own namespace, so they never meet a `command_id` in a shared store. Every event has its own `event_id`, and every consumer its own name, so the events one command raises are each handled, and several handlers of one event type can share a store. An inbound event's `event_id` is derived from the integration event's by the [`InboundEventGateway`](../infrastructure/inbound-event-gateway.md#event-identity), so each redelivery of one message has one key. `causation_id` plays no part. In-process and inbound events follow the same rule: one event object dispatched twice is handled once.

`consumer` is optional until 0.5.0. Left out, the behavior uses the handler's `module:qualname` (the type's, for a callable instance), and the constructor emits a `DeprecationWarning`. Those names collide: one function in two registrations, two lambdas in one scope, every `functools.partial`, the bound methods of two instances and two callable instances of one class share one. Renaming a handler, or its `consumer`, changes its keys, so the events it handled before run again.

One instance serves one handler. The behavior learns its handler at the first dispatch, and the first dispatch to a different handler raises `EventIdempotencyMisuseError` before any store call; that handler does not run. Handlers compare by identity first and then with `==`, so one function registered twice, or two callable instances that compare equal, count as one handler and are not detected.

**The flow with a reserving store** (`MessageReservationStore`, as `FakeProcessedMessageStore` implements):

```
1. Event arrives; key = uuid5(consumer namespace, event_id)
2. store.get(key) hit → skip the handler, INFO "Skipping duplicate event OrderPlaced (event_id=…)"
3. store.reserve(key)
   - no token: get again; skip if the first delivery completed meanwhile,
     otherwise EventInProgressError (handler skipped)
4. next(): the handler runs
5. It returned → store.set(key, True), which completes the reservation
   It raised   → store.release(key, token), then the error propagates unchanged
```

**The flow without one:** `store.contains(key)` hit → skip; otherwise the handler, then `store.check_and_set(key)`, protected from cancellation. Two concurrent deliveries can both run the handler.

| When | What happens |
|------|--------------|
| The handler raised, or was cancelled | With a reserving store the reservation is released; either way the event stays unmarked, and the handler's own error leaves the pipeline unchanged; a redelivery runs the handler again |
| A delivery arrives while the first one runs (with a reserving store) | `EventInProgressError`, naming the event type only; the handler does not run |
| `set` or `check_and_set` raised after the handler succeeded | A WARNING on `pydomain.pipeline` naming the error's type only (`Idempotency record failed for OrderPlaced: ConnectionError`). The delivery returns normally. With a reserving store the key stays reserved until the TTL, so an immediate duplicate is refused; after it, a redelivery runs the handler again |
| `release` raised | A note on the handler's error, `clean-up also failed: <type>`; the key stays reserved until the TTL |
| The dispatch was closed while suspended (`GeneratorExit`) | Nothing is awaited, so the reservation stays until the TTL |

Every store call that changes state, `reserve`, `release` and `set`, and without reservations `check_and_set`, runs protected from cancellation, as for commands, so the store must work from any task; the reads, `get` and `contains`, are not. Through `EventBus`, a handler's failure, an `EventInProgressError` and an `EventIdempotencyMisuseError` are logged at ERROR and swallowed, like any handler failure, and the other handlers still run. The inbound gateway therefore acknowledges the message, and a redelivery after a failure needs a broker or subscriber that redelivers on its own; a NACK on failure is planned for a later release.

Event deduplication is at-least-once too: the handler's work and its mark are not atomic, so a crash between them runs the handler again on redelivery.

```python
from uuid import uuid4

from pydomain.cqrs import EventBus
from pydomain.cqrs.behaviors import EventIdempotencyBehavior
from pydomain.ddd import DomainEvent
from pydomain.testing import FakeProcessedMessageStore


class InvoiceIssued(DomainEvent):
    invoice_id: UUID


async def test_event_idempotency() -> None:
    store = FakeProcessedMessageStore()  # shared by both handlers
    attempts: list[str] = []

    async def post_to_ledger(event: InvoiceIssued) -> None:
        attempts.append("ledger")
        if attempts.count("ledger") == 1:
            raise ConnectionError("synthetic outage")

    async def send_receipt(event: InvoiceIssued) -> None:
        attempts.append("receipt")

    bus = EventBus()
    bus.register(
        InvoiceIssued,
        post_to_ledger,
        behaviors=[EventIdempotencyBehavior(store, consumer="ledger")],
    )
    bus.register(
        InvoiceIssued,
        send_receipt,
        behaviors=[EventIdempotencyBehavior(store, consumer="receipts")],
    )

    event = InvoiceIssued(invoice_id=uuid4())
    await bus.dispatch(event)  # the ledger fails (logged), the receipt is sent
    await bus.dispatch(event)  # redelivered: the ledger runs again, the receipt is skipped
    await bus.dispatch(event)  # redelivered again: both are skipped

    assert attempts == ["ledger", "receipt", "ledger"]
```

<!-- docs-test: check
await test_event_idempotency()
-->

## Distributed Locking

### The Problem

Two concurrent commands targeting the same aggregate can interleave, causing lost updates or invariant violations.

### The Solution

`AggregateLockingBehavior` acquires a lock keyed by aggregate ID before the handler runs, ensuring only one command modifies a given aggregate at a time.

The locks are held until the command has committed: `AggregateLockingBehavior` releases them after `next()`, which includes the commit. A second command on the same key therefore reads what the first one committed. Give a lock lease or TTL room for the commit as well as the handler.

Locking does not make a repository shared by concurrent units of work safe. The locks are per aggregate key, so commands on different aggregates still share the repository's event buffer, and the locks are released before the unit of work's exit drains that buffer (a known limit, planned for a later release). Build the repository per unit of work when dispatches run concurrently ([Unit of Work](unit-of-work.md#rollback), ADR-068).

### LockProvider

```python
from pydomain.cqrs.locking import LockProvider


class LockProvider(Protocol):
    async def acquire(self, key: str) -> None:
        """Acquire the lock, blocking until held."""
        ...

    async def release(self, key: str) -> None:
        """Release the lock."""
        ...
```

Three promises go with the signatures, and `LockProviderContract` checks each against every provider (ADR-076):

- An `acquire()` cancelled while it waits never holds the key.
- Locks are **not reentrant**: a second `acquire()` of a key from the task that holds it is never granted. A provider may raise (`FakeLockProvider` raises `LockReentryError`) or block, but must keep the first hold.
- `release()` is **ownership-safe**: a release of a hold that already ended never frees the lock another holder took since. It may do nothing or raise.

### LockKeyResolver

```python
from pydomain.cqrs.locking import LockKeyResolver


class LockKeyResolver(Protocol):
    def resolve(self, message: Any) -> list[str]:
        """Return lock keys for the message. Empty list = no locking."""
        ...
```

### Deadlock Prevention

Keys are acquired in **sorted** order (ADR-019), so two commands that need the same keys never wait on each other in opposite order.

### Release Guarantees

Every key whose `acquire()` returned is released, in **reverse** order, however the command ends (ADR-076):

- after the handler returns and the unit of work commits;
- after the handler, the commit or an `acquire()` raises;
- after a cancellation, including one that arrives while the behavior waits on its second key.

The releases run in the task that acquired the locks, so a provider whose locks belong to that task (an `anyio.Lock`, a client that checks the current task) works. If that task is cancelled while a release runs, for example by a second `cancel()` or an anyio cancel scope, the interrupted key and the keys after it move to one background task, and the cancellation propagates at once; the caller does not wait for that task. This holds whether the provider re-raises the cancellation, converts it into its own error or swallows it. A task-owned provider whose `release()` awaits is interrupted and refuses these background releases, so give it a lease or TTL for this case.

The background task calls the interrupted release **again**, because the interruption may have come after the release took effect. So `release()` must be **ownership-safe, not just idempotent**: if the key was freed and another command took it, the repeated call must not free that command's lock, or two commands hold the key at once. Tie each hold to its acquire with a token, as `FakeLockProvider` does and the [Redis example](../../how-to/cqrs/add-distributed-locking.md#production-lock-provider-redis-example) shows (ADR-076).

The cancellation handling needs asyncio. Under another anyio backend, such as trio, the behavior releases every key inline, with no hand-over, so a cancellation during the releases can interrupt them.

Each `release()` is isolated, so one that fails does not skip the others. A failure is reported by its exception type only, never the lock key, which is built from the message:

| When | What you see |
|------|--------------|
| The handler and the commit succeeded and a release raised | The command still succeeds: the commit has already happened. A WARNING on `pydomain.pipeline`: `Lock release failed for ReserveStock: ConnectionError` |
| The handler or the commit raised and a release raised too | That error, with a note `clean-up also failed: ConnectionError` per failed release |
| A release raised a `BaseException` that is not an `Exception` (a cancellation) | That exception, once every other release ran |
| The caller was cancelled during the releases | The `CancelledError`, with a note per release that had already failed; a WARNING per failed release from the background task: `Abandoned lock release for ReserveStock failed with ConnectionError` |

A lock whose release failed is the provider's to recover, typically through a lease or TTL.

### Reentrancy

Locks are **not reentrant**. A handler must not dispatch a nested command whose keys include one it already holds:

- with `FakeLockProvider`, the nested acquire raises `LockReentryError` (a `CQRSError`), which reaches the caller wrapped in `CommandExecutionError`;
- with another provider, it may block forever;
- a nested dispatch started in a **new task** (`asyncio.create_task`, a task group) is a different task, so it waits on the outer command's lock while the outer command waits on it, and deadlocks with every provider.

Dispatch the second command after the first returns, or give the commands different keys.

### `DictLockKeyResolver`

A registry-based resolver that maps message types to key-extraction functions:

```python
from pydomain.cqrs.locking import DictLockKeyResolver

resolver = DictLockKeyResolver()
resolver.register(PlaceOrder, lambda cmd: [f"order:{cmd.order_id}"])
resolver.register(CancelOrder, lambda cmd: [f"order:{cmd.order_id}"])
```

## Pipeline Slot Order

> **Concurrent duplicates:** with a store that implements `MessageReservationStore`, such as `FakeProcessedMessageStore`, a concurrent duplicate gets `CommandInProgressError` before it reaches the locks. With a store that implements only `ProcessedMessageStore`, two concurrent dispatches of the same `command_id` can both run the handler, because idempotency runs before locking.

In the recommended pipeline configuration, idempotency (slot 3) runs before locking (slot 4):

```
1. LoggingBehavior
2. ValidationBehavior
3. IdempotencyBehavior   ← duplicate check and reservation here
4. AggregateLockingBehavior  ← lock here (skipped if duplicate)
—  Terminal step: handler, then commit
   then, unwinding: release the locks, record the result (or release the reservation)
```

This ordering avoids wasted lock acquisition on commands that will be skipped. Because the commit runs in the terminal step, the locks are released and the result is recorded only after the commit.

## Next Steps

- **[Add Idempotency →](../../how-to/cqrs/add-idempotency.md)** — step-by-step configuration
- **[Add Distributed Locking →](../../how-to/cqrs/add-distributed-locking.md)** — locking setup
- **[Pipeline Behaviors →](pipeline-behaviors.md)** — how behaviors compose
- **[Fake Processed Message Store →](../../how-to/testing/use-fake-processed-message-store.md)** — testing idempotency
