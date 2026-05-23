# Event Store

> **Adoption Level:** 4 — Event Sourcing
> **Module:** `pydomain.es.event_store`
> **Prerequisites:** [Event Sourcing](event-sourcing.md), [Event Stream](event-stream.md)

## What is an Event Store?

An **EventStore** is the append-only persistence contract for event-sourced [Aggregates](event-sourced-aggregates.md). It provides two core operations: append new events to a stream (with optimistic concurrency control) and read events from a stream.

The EventStore is a `Protocol` — any storage backend that satisfies this contract is a valid event store. The library provides a `FakeEventStore` for testing; production backends (PostgreSQL, EventStoreDB, etc.) are implemented by the application.

## The `EventStore` Protocol

```python
from typing import Protocol, runtime_checkable
from collections.abc import Sequence
from uuid import UUID


@runtime_checkable
class EventStore(Protocol):
    async def append_to_stream(
        self,
        aggregate_id: str,
        events: Sequence[DomainEvent],
        expected_version: int,
        command_id: UUID | None = None,
    ) -> None: ...

    async def read_stream(
        self,
        aggregate_id: str,
        from_version: int = 0,
    ) -> EventStream: ...

    async def read_all(self, from_version: int = 0) -> EventStream: ...
```

## `append_to_stream` — Write

Appends events to a stream if the expected version matches the current stream length:

| Parameter | Purpose |
|-----------|---------|
| `aggregate_id` | The aggregate / stream identity |
| `events` | The domain events to persist |
| `expected_version` | The number of events currently in the stream (0 for a new stream) |
| `command_id` | Optional UUID for idempotency — store SHOULD reject duplicates |

**Errors:**

| Error | When |
|-------|------|
| `ConcurrencyError` | The current stream length does not match `expected_version` |
| `DuplicateCommandError` | `command_id` was already processed for this aggregate |

## `read_stream` — Read by Aggregate

Returns an `EventStream` for the given aggregate, optionally starting from a specific version (used for snapshot-based hydration to only replay tail events).

Raises `StreamNotFoundError` if the stream does not exist.

## `read_all` — Read Global Log

Returns all events across all aggregates in append order. Used by subscriptions and catch-up projections to process events globally. The returned `EventStream.version` is the total global event count.

## Optimistic Concurrency

The `expected_version` parameter implements optimistic concurrency control: the caller declares how many events it believes are in the stream. If another process appended events between the read and the write, the store rejects the append with `ConcurrencyError`. The caller must re-read and retry.

```python
# Version mismatch — another process appended first
await store.append_to_stream("order-42", events, expected_version=5)
# Raises ConcurrencyError: actual version is 6
```

## Idempotency via `command_id`

When a `command_id` is provided, the event store can reject duplicate command submissions. This is the persistence-layer defense against double-processing, complementing the [Idempotency Behavior](../cqrs/idempotency-and-locking.md) at the command bus level.

## The `FakeEventStore` for Testing

```python
from pydomain.testing.fake_event_store import FakeEventStore

store = FakeEventStore()
await store.append_to_stream("order-42", events, expected_version=0)
stream = await store.read_stream("order-42")  # EventStream(events=[...], version=1)
```

The fake store is fully in-memory with no serialization round-trip. See [Test Your Application recipe](../../how-to/recipes/test-your-application.md) for the full testing setup.

## Relationship to other concepts

- **EventSourcedRepository**: the primary consumer — calls `append_to_stream` on save, `read_stream` on load
- **Subscriptions**: call `read_all` to catch up on global events
- **CheckpointStore**: persists the `from_version` offset for `read_all` callers

## Next steps

- [Event-Sourced Repositories](event-sourced-repositories.md) — how repositories use the store
- [How to connect an event store](../../how-to/event-sourcing/connect-event-store.md) — wiring guide
- [Projections](projections.md) — consuming events from the store
