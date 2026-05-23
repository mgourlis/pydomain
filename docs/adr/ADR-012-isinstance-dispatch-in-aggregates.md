# ADR-012: isinstance Dispatch in Aggregate `_when()`

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

`EventSourcedAggregateRoot._when(event)` must route each event to the correct mutation logic. The alternatives are: (a) `isinstance` checks in user code, (b) a registry mapping event types to handlers, (c) convention-based method dispatch like `_when_{EventType}`.

Aggregates are typically small — a well-bounded aggregate handles 3–8 event types.

## Decision

Use explicit `isinstance` checks in user-written `_when()` methods. Do not provide a registry or convention dispatch for aggregates.

Convention dispatch (`_when_{EventType}`) *is* used for `EventSourcedProjection`, where a projection may handle 20+ event types from multiple aggregates.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Event registry | Adds infrastructure into the aggregate; coupling to registration mechanism; for 5 events, more ceremony than value |
| Convention dispatch (`_when_OrderPlaced`) | Requires `getattr()` lookup on every event; unnecessary indirection for small handler counts; hides the event contract |

## Consequences

### Positive

- The aggregate's event contract is immediately visible — no hidden handlers.
- No infrastructure mechanism leaks into the domain layer.
- Simple and readable for the typical 3–8 event types per aggregate.

### Negative

- Boilerplate `if/elif` chain grows with event count (acceptable for aggregates, which are bounded by design).

### Neutral

- Projections use convention dispatch because they handle many more event types. This is a deliberate asymmetry, not an inconsistency.

## References

- §9.4 isinstance Dispatch in `_when()`
