# Event Stream

> **Adoption Level:** 4 — Event Sourcing
> **Module:** `pydomain.es.event_stream`
> **Prerequisites:** [Event Sourcing](event-sourcing.md), [Domain Events](../ddd/domain-events.md)

## What is an EventStream?

An **EventStream** is a frozen, read-only snapshot of an event stream at a point in time. It bundles a sequence of [Domain Events](../ddd/domain-events.md) with the current version number of the stream.

It is the return type of `EventStore.read_stream()` and `EventStore.read_all()` — the caller receives both the events and the stream's position in a single immutable object.

## The `EventStream` Model

```python
from pydantic import BaseModel, ConfigDict
from collections.abc import Sequence
from pydomain.ddd.domain_event import DomainEvent


class EventStream(BaseModel):
    events: Sequence[DomainEvent]
    version: int

    model_config = ConfigDict(frozen=True)
```

| Field | Type | Purpose |
|-------|------|---------|
| `events` | `Sequence[DomainEvent]` | The events in ascending version order |
| `version` | `int` | The total number of events in the stream at the time of the read |

## When `version` Means Different Things

The meaning of `version` depends on which read method produced the stream:

| Method | `version` Meaning |
|--------|------------------|
| `read_stream(aggregate_id)` | Number of events for that specific aggregate |
| `read_all()` | Total number of events in the global event log |

For `read_stream`, the version is the aggregate's version — used for optimistic concurrency control when appending. For `read_all`, it's the global position — used by subscriptions to track catch-up progress.

## Why Frozen?

`EventStream` is frozen (`ConfigDict(frozen=True)`) because it represents a point-in-time read. Mutating it would not affect the underlying store, so the model enforces immutability at the type level. This also makes it safe to pass across async boundaries.

## Usage in Repositories

The repository iterates over `stream.events` to rebuild aggregate state:

```python
async def get_by_id(self, id_: UUID) -> Order | None:
    stream = await self._event_store.read_stream(str(id_))
    aggregate = Order(id=id_)
    for event in stream.events:
        aggregate._replay(event)
    return aggregate
```

The `stream.version` is used for the optimistic concurrency check on the next `save`:

```python
expected_version = aggregate.version - len(new_events)
await event_store.append_to_stream(aggregate_id, new_events, expected_version)
```

## Relationship to other concepts

- **Event Store**: produces `EventStream` via `read_stream()` and `read_all()`
- **EventSourcedRepository**: consumes `EventStream` to rebuild aggregates
- **Subscriptions**: use `read_all()` with checkpoint tracking for catch-up

## Next steps

- [Event Store](event-store.md) — the protocol that produces event streams
- [Event-Sourced Repositories](event-sourced-repositories.md) — how repositories consume streams
- [Projections](projections.md) — building read models from event streams
