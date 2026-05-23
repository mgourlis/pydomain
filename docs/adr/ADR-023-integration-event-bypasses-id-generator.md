# ADR-023: IntegrationEvent Bypasses IdGenerator — Uses `uuid_utils.uuid7` Directly

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

`IntegrationEvent` differs from `DomainEvent` in its identity approach:

- `DomainEvent` uses `event_id: UUID` with `IdGenerator[UUID]` (via `DomainEvent._id_generator`).
- `IntegrationEvent` must use `event_id: str` because it carries primitives only (ADR-022) — `UUID` is not a primitive type suitable for cross-boundary serialization.

The `IdGenerator[TId]` Protocol generates typed IDs, but `IntegrationEvent` needs a `str` representation. Using `IdGenerator` would require a `str`-typed generator or a conversion step.

## Decision

`IntegrationEvent` uses `uuid7()` directly and casts to `str`, bypassing the `IdGenerator` Protocol entirely:

```python
from uuid_utils import uuid7

class IntegrationEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid7()))
    occurred_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
```

This differs from `DomainEvent`:
- `DomainEvent.event_id: UUID` (typed, uses `IdGenerator[UUID]`)
- `IntegrationEvent.event_id: str` (primitive, uses `uuid7()` directly)

Both use UUIDv7 for time-ordering, but the type representation differs because integration events must be primitive-only.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| `IdGenerator[str]` for integration events | Requires configuring a separate generator; `str(uuid7())` is trivially simple; adds configuration ceremony for no benefit |
| `event_id: UUID` (same as DomainEvent) | Violates ADR-022: `UUID` is not a primitive type; would break broker serialization |
| `event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))` | UUIDv4 is random — loses time-ordering benefits of UUIDv7 |

## Consequences

### Positive

- `event_id` is always a string — compatible with any message broker format.
- UUIDv7 provides time-ordering despite the `str` representation.
- No `IdGenerator` configuration needed for integration events.

### Negative

- Divergent ID generation between `DomainEvent` (via `IdGenerator`) and `IntegrationEvent` (direct `uuid7()`).
- `str` representation loses UUID-specific operations (version extraction, variant checking).

### Neutral

- The `occurred_at` field is also `str` (ISO 8601) instead of `datetime` — consistent with the primitive-only constraint.

## References

- `src/pydomain/cqrs/integration_events.py` — `IntegrationEvent` class
- ADR-022: Integration Events — Primitive-Only Payloads
- ADR-008: UUIDv7 for Time-Ordered Identity Generation
