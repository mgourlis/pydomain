# ADR-048: SubscriptionRunner At-Least-Once Delivery

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Projections must process the global event log to build read models. The subscription runner must:
1. Track position (checkpoint) in the event stream per subscription.
2. Deliver events to projections reliably — no events should be permanently lost.
3. Handle failures gracefully — a failed batch should be retried, not skipped.

## Decision

`SubscriptionRunner` is an abstract base class implementing **at-least-once** delivery semantics:

```python
class SubscriptionRunner(ABC):
    def __init__(self, event_store, checkpoint_store, *,
                 poll_interval_seconds=1.0, failure_backoff_seconds=0.1): ...

    async def run(self) -> None:
        """Polling loop — runs until stop() is called."""
        while not self._stop_requested:
            had_events = await self._process_cycle()
            if not had_events:
                await asyncio.sleep(self._poll_interval_seconds)

    async def run_once(self) -> None:
        """Single catch-up pass — useful for tests."""
        await self._process_cycle()
```

**At-least-once guarantee**: The checkpoint is updated **after** `process_batch()` succeeds:

```python
async def _dispatch_to_subscription(self, subscription, stream, ...):
    matching = [e for e in events if isinstance(e, subscription.event_types)]
    if matching:
        try:
            await self.process_batch(matching, subscription)
        except Exception:
            logger.warning("Batch failed; will retry")
            await asyncio.sleep(self._failure_backoff_seconds)
            return  # Checkpoint NOT updated → events will be redelivered

    await self._checkpoint_store.save(subscription.subscription_id, stream.version)
```

**Failure handling**: If `process_batch()` raises, the checkpoint is not updated. On the next cycle, the same events are re-delivered. After a configurable backoff delay.

**Event filtering**: Each `Subscription` declares its event types:

```python
@dataclass
class Subscription:
    subscription_id: str
    projection: EventSourcedProjection
    event_types: tuple[type[DomainEvent], ...]
```

Only matching events are dispatched to the projection.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Exactly-once delivery | Requires distributed transactions or idempotent consumers; complex; rarely needed |
| At-most-once (update checkpoint before processing) | Events may be permanently lost on failure |
| Push-based subscriptions (broker pushes events) | Requires a running broker; not suitable for testing or simple deployments |

## Consequences

### Positive

- No event loss — failed batches are retried automatically.
- Checkpoint persists progress across restarts.
- `run_once()` enables deterministic testing without a polling loop.
- Event type filtering prevents irrelevant events from reaching projections.
- Single global read (`read_all`) shared across all subscriptions — efficient.

### Negative

- At-least-once means events may be processed more than once — projections must be idempotent.
- Polling-based — slight latency between event append and projection update.

### Neutral

- `process_batch()` is abstract — subclasses define how events are applied to projections.

## References

- `src/pydomain/infrastructure/subscription.py` — `SubscriptionRunner`, `Subscription`
- `src/pydomain/es/checkpoint_store.py` — `CheckpointStore` Protocol
- `src/pydomain/es/event_store.py` — `EventStore.read_all()` global event log
