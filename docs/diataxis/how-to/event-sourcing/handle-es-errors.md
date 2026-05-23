# How to Handle Event Sourcing Errors

> **Adoption Level:** 4 · Prerequisites: [Event Store concept](../../concepts/es/event-store.md), [Event Versioning concept](../../concepts/es/event-versioning.md), [Domain Errors how-to](../../how-to/ddd/handle-domain-errors.md)

This guide shows you how to handle the errors specific to event-sourced aggregates and repositories.

## 1. ConcurrencyError — Optimistic concurrency conflicts

When two commands modify the same aggregate concurrently, the second append fails:

```python
from pydomain.ddd.exceptions import ConcurrencyError


async def handle_place_order(cmd: PlaceOrder, repo, bus) -> PlaceOrderResult:
    max_retries = 3
    for attempt in range(max_retries):
        try:
            order = await repo.get_by_id(cmd.order_id)
            if order is None:
                raise OrderNotFoundError(cmd.order_id)
            order.place()
            await repo.save(order, command_id=cmd.command_id)
            return PlaceOrderResult(order_id=order.id, status=order.status)
        except ConcurrencyError:
            if attempt == max_retries - 1:
                raise  # Rethrow after exhausting retries
            # Retry: re-read aggregate and try again
```

The retry strategy re-reads the aggregate (getting the latest events) and replays the mutation. After the max retries, bubble up the error.

## 2. DuplicateCommandError — Idempotency guard

When a command is retried due to a network error but was already processed:

```python
from pydomain.es.exceptions import DuplicateCommandError


async def handle_place_order(cmd: PlaceOrder, repo, bus) -> PlaceOrderResult:
    try:
        order = await repo.get_by_id(cmd.order_id)
        order.place()
        await repo.save(order, command_id=cmd.command_id)
        return PlaceOrderResult(order_id=order.id, status=order.status)
    except DuplicateCommandError:
        # Command already processed — return the existing result
        # (requires result caching or idempotent result lookup)
        return await load_existing_result(cmd.command_id)
```

## 3. StreamNotFoundError — Missing event stream

When loading an aggregate that was never created:

```python
from pydomain.es.exceptions import StreamNotFoundError

# The repository handles this for you — get_by_id returns None:
order = await repository.get_by_id(unknown_id)
if order is None:
    raise OrderNotFoundError(unknown_id)
```

If you call `event_store.read_stream()` directly, `StreamNotFoundError` is raised:

```python
try:
    stream = await event_store.read_stream(aggregate_id)
except StreamNotFoundError:
    # Aggregate doesn't exist yet — treat as new
    aggregate = Order(id=aggregate_id)
```

## 4. UpcastError — Failed event transformation

When an upcaster's `_transform` fails:

```python
from pydomain.es.exceptions import UpcastError


try:
    transformed = await apply_upcaster_chain(event_data, upcaster_registry)
except UpcastError as exc:
    logger.error(
        "Failed to upcast event",
        extra={"event_type": event_data.get("event_type"), "error": str(exc)},
    )
    # Option: send to dead-letter queue for manual repair
    await dead_letter_queue.send(event_data, error=str(exc))
```

Fix the root cause by updating the `_transform` implementation to handle the unexpected payload shape, or by repairing the stored event.

## 5. StaleSnapshotError — Schema version mismatch

When a snapshot's schema doesn't match the aggregate's current schema:

```python
from pydomain.es.exceptions import StaleSnapshotError

# By default, the repository silently falls back to full replay.
# If you want to detect this for monitoring:
class LoggingSnapshotPolicy:
    def should_use_snapshot(self, snapshot, expected_schema_version) -> bool:
        if snapshot.schema_version != expected_schema_version:
            logger.warning(
                "Stale snapshot detected",
                extra={
                    "aggregate_id": snapshot.aggregate_id,
                    "snapshot_version": snapshot.schema_version,
                    "expected_version": expected_schema_version,
                },
            )
            return False
        return True
```

The repository automatically falls back to full event replay when the snapshot is rejected.

## Expected outcome

Robust error handling for the four event-sourcing-specific error types: concurrency conflicts (retry), duplicate commands (return existing result), missing streams (treat as new aggregate or not-found), and upcast failures (dead-letter for manual repair).

## Next steps

- [Implement an ES Repository](implement-es-repository.md) — where most of these errors originate
- [Implement an Upcaster](implement-upcaster.md) — UpcastError source
- [ES with CQRS recipe](../../how-to/recipes/es-with-cqrs.md) — full integration pattern

## Cross-references

- **ADR-042**: Upcaster chain with cycle detection
- **ADR-043**: Snapshot policy as pluggable Protocol
- **ADR-053**: Snapshot schema version policy
