# ADR-036: Saga Idempotency via `processed_event_ids` Set

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

In at-least-once delivery systems, the same domain event may be delivered to a saga multiple times. Without deduplication:

1. The same event triggers the saga handler twice.
2. Two compensating commands are pushed for the same step.
3. The saga state is corrupted with duplicate step records.

Event-level idempotency must be enforced at the saga level, not just at the infrastructure level.

## Decision

`SagaState` maintains a `processed_event_ids` set for O(1) duplicate detection:

```python
class SagaState(AggregateRoot[UUID]):
    processed_event_ids: set[UUID] = Field(default_factory=set)

    def is_event_processed(self, event_id: UUID) -> bool:
        return event_id in self.processed_event_ids

    def mark_event_processed(self, event_id: UUID) -> None:
        self.processed_event_ids.add(event_id)
        self._enforce_max_processed_events()
```

The saga's `handle()` method checks before processing:

```python
async def handle(self, event: DomainEvent) -> None:
    if self.state.is_terminal:
        return
    if self.state.is_event_processed(event.event_id):
        return  # Skip duplicate

    # Process event...
    self.state.mark_event_processed(event.event_id)
    self.state.record_step(...)
```

**Memory bounds**: `max_processed_events` ClassVar caps the set size. When the limit is exceeded, oldest entries are discarded. Default is `0` (unlimited) for backward compatibility.

**Serialization**: The set is serialized as a list (JSON/DB compatibility) via `@field_serializer` and deserialized back to a set via `@field_validator`.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| No saga-level idempotency | Duplicate events corrupt saga state; compensations are duplicated |
| Infrastructure-level idempotency only | Cannot guarantee deduplication across crash recovery boundaries |
| List of processed IDs (not set) | O(n) lookup for every event; performance degrades with saga length |
| Bloom filter | Probabilistic — false positives would skip legitimate events |

## Consequences

### Positive

- O(1) duplicate detection — constant-time check regardless of saga length.
- Saga-level idempotency is self-contained — no dependency on infrastructure idempotency.
- Memory bounds prevent unbounded growth for long-lived sagas.
- Set-based storage ensures no duplicate entries.

### Negative

- `processed_event_ids` grows with the number of events the saga handles (mitigated by `max_processed_events` cap).
- Set is unordered — eviction when capped discards arbitrary entries, not necessarily oldest.

### Neutral

- This is saga-level idempotency, complementing but not replacing infrastructure-level idempotency (ADR-018).

## References

- `src/pydomain/cqrs/saga/state.py` — `SagaState.processed_event_ids`, `is_event_processed()`, `mark_event_processed()`
- `src/pydomain/cqrs/saga/saga.py` — `Saga.handle()` checks `is_event_processed()` before processing
- ADR-018: MISSING Sentinel for Idempotency
