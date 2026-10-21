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

When an upcaster's `_transform` fails, or when `EventRegistry.deserialize` finds that the upcaster chain does not end at the event class's version (a missing upcaster, [ADR-069](../../../adr/ADR-069-upcast-to-class-version-once.md)):

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

`UpcastError`'s message names the event type, the versions and the failure's type, never payload values, but its `__cause__` is your upcaster's own exception and may quote them: keep values out of the messages you raise, and log the error without its traceback if the dead-letter queue or the log is less protected than the event store. See [Handle Sensitive Payloads](../infrastructure/handle-sensitive-payloads.md).

Fix the root cause by updating the `_transform` implementation to handle the unexpected payload shape, or by repairing the stored event.

## 5. StaleSnapshotError — Schema version mismatch

The repository uses every snapshot it finds unless you configure a `SnapshotSchemaPolicy` (ADR-053). With no
policy there is no fallback: a snapshot taken before a schema change is restored as it is. Configure
`RejectStaleSnapshotPolicy`, and bump `_snapshot_schema_version` when the aggregate's fields change, to make the
repository skip a stale snapshot and rebuild the aggregate by full replay. The repository never raises
`StaleSnapshotError` itself; raise it from your own policy or code if you want a hard failure instead of the
replay.

To see how often stale snapshots are skipped, wrap the check in a policy that logs ids and versions only, never
the snapshot's state:

```python
import logging

from pydomain.es.snapshot import Snapshot

logger = logging.getLogger("snapshots")


class LoggingSnapshotPolicy:
    def should_use_snapshot(
        self, snapshot: Snapshot, expected_schema_version: int
    ) -> bool:
        if snapshot.schema_version != expected_schema_version:
            logger.warning(
                "Stale snapshot skipped",
                extra={
                    "aggregate_id": snapshot.aggregate_id,
                    "snapshot_schema_version": snapshot.schema_version,
                    "expected_schema_version": expected_schema_version,
                },
            )
            return False
        return True
```

Pass it as `EventSourcedRepository(..., snapshot_schema_policy=LoggingSnapshotPolicy())`. When it returns
`False`, the repository replays the full stream.

## 6. SnapshotRestoreError — Snapshot state that no longer fits

When the repository finds a snapshot it may use, it validates the snapshot's state into the aggregate class
([ADR-075](../../../adr/ADR-075-restore-snapshots-by-validation.md)). If that raises any exception, because a
stored value no longer validates, a new required field has no default, an `extra="forbid"` aggregate meets a
removed field's key, or your own validator or `model_post_init` raises, `get_by_id()` raises
`SnapshotRestoreError`. It does not fall back to a full replay.

```python
from pydomain.es import SnapshotRestoreError

try:
    order = await repository.get_by_id(order_id)
except SnapshotRestoreError as error:
    logger.error(
        "Snapshot could not be restored",
        extra={
            "aggregate_id": error.aggregate_id,
            "snapshot_version": error.snapshot_version,
            "reason": str(error),
        },
    )
    raise
```

- **Attributes:** `aggregate_id` and `snapshot_version`. It is a `DomainError`.
- **No values in it:** the message names the aggregate, the id, the snapshot version and either the failing
  locations with their error codes (`ValidationError: 1 error for Order at total (int_parsing)`) or the type of
  the exception your code raised. Locations can include a dict field's keys. The error has no cause and no
  context, so a traceback or `logger.exception()` shows no stored value either; logging `str(error)` is safe.
- **Remedies:** bump `_snapshot_schema_version` and configure `RejectStaleSnapshotPolicy`, so stale snapshots are
  skipped and the aggregate is rebuilt by full replay (§5); give a new field a default; or overwrite the snapshot
  by saving the aggregate again after a successful load. Fix a validator or `model_post_init` that raises on
  state the aggregate itself produced.

## Expected outcome

Robust error handling for the six event-sourcing-specific error types: concurrency conflicts (retry), duplicate commands (return existing result), missing streams (treat as new aggregate or not-found), upcast failures (dead-letter for manual repair), stale snapshots (skipped by a schema policy) and snapshots that can no longer be restored (bump the schema version, or fix the state).

## Next steps

- [Implement an ES Repository](implement-es-repository.md) — where most of these errors originate
- [Implement an Upcaster](implement-upcaster.md) — UpcastError source
- [ES with CQRS recipe](../../how-to/recipes/es-with-cqrs.md) — full integration pattern

## Cross-references

- **ADR-042**: Upcaster chain with cycle detection
- **ADR-043**: Snapshot policy as pluggable Protocol
- **ADR-053**: Snapshot schema version policy
- **ADR-075**: Restore snapshots by validation
