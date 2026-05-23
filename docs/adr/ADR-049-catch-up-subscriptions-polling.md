# ADR-049: Catch-Up Subscriptions via Polling `SubscriptionRunner`

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Event-sourced projections must "catch up" to the current state of the event log. When a projection starts (or restarts after a crash), it reads all events from its last checkpoint to the current head of the global log.

The mechanism must be:
1. **Polling-based** — no dependency on a push-based broker.
2. **Checkpoint-tracked** — each subscription maintains its own position.
3. **Efficient** — one global read shared across all subscriptions per cycle.

## Decision

`SubscriptionRunner` implements catch-up via polling:

### Processing Cycle

1. **Load all checkpoints**: Each subscription's checkpoint is loaded from `CheckpointStore`.
2. **Single global read**: `event_store.read_all(from_version=min_checkpoint)` — reads from the furthest-behind position.
3. **Per-subscription dispatch**: Each subscription gets a slice of the stream from its own checkpoint, filtered by its `event_types`.
4. **Checkpoint update**: After successful `process_batch()`, save `stream.version` as the new checkpoint.

```python
async def _process_cycle(self) -> bool:
    checkpoints = {sub_id: await store.load(sub_id) for sub_id in subscriptions}
    min_checkpoint = min(checkpoints.values())
    stream = await self._event_store.read_all(from_version=min_checkpoint)

    for sub in self._subscriptions.values():
        offset = checkpoints[sub.subscription_id] - min_checkpoint
        sub_events = stream.events[offset:]
        matching = [e for e in sub_events if isinstance(e, sub.event_types)]
        if matching:
            await self.process_batch(matching, sub)
        await self._checkpoint_store.save(sub.subscription_id, stream.version)
```

### Configuration

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `poll_interval_seconds` | 1.0 | Sleep between cycles when no events found |
| `failure_backoff_seconds` | 0.1 | Delay after a failed batch before retry |

### Run Modes

- `run()`: Continuous polling loop until `stop()` is called.
- `run_once()`: Single catch-up pass — for tests and controlled invocations.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Push-based (broker notifies on new events) | Requires running broker; not testable without infrastructure |
| Per-subscription reads (each reads from its own checkpoint) | N subscriptions = N database reads; wasteful when subscriptions share most of the stream |
| Event store notifies runners directly | Couples event store to subscription runner; violates layer boundaries |

## Consequences

### Positive

- One global read per cycle — efficient when multiple subscriptions share most of the event stream.
- Catch-up is automatic — new or restarted projections read from their last checkpoint.
- Polling works with any `EventStore` implementation — no broker dependency.
- `run_once()` is deterministic — ideal for testing.

### Negative

- Polling latency — projections are at most `poll_interval_seconds` behind the event log.
- `min_checkpoint` means all subscriptions re-read from the furthest-behind position — slightly wasteful when subscriptions are at different positions.

### Neutral

- The global read approach assumes `read_all()` is efficient for the storage backend.

## References

- `src/pydomain/infrastructure/subscription.py` — `SubscriptionRunner._process_cycle()`
- `src/pydomain/es/event_store.py` — `EventStore.read_all()`
- `src/pydomain/es/checkpoint_store.py` — `CheckpointStore`
