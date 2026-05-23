# ADR-028: Saga `on()` DSL for Unified Command and Compensation

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

For each event a saga handles, the user typically needs to: (1) map the event to a command, (2) register a compensating action, (3) update the step name. These three concerns are tightly coupled — they always refer to the same event type.

## Decision

The `on()` DSL captures all three concerns in a single declaration:

```python
self.on(OrderCreated,
    send=lambda e: ReserveItems(order_id=e.order_id),
    step="reserving",
    compensate=lambda e: CancelReservation(order_id=e.order_id))
```

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Separate `on_event()` + `compensate()` registration | Temporal coupling — developer must remember both; compensation references same event type/fields, so it repeats information; step-level grouping is lost |

## Consequences

### Positive

- Forward command and compensation are paired explicitly — impossible to forget one.
- The saga constructor reads as a declarative process definition.
- LIFO compensation order naturally mirrors forward step order.

### Negative

- The `on()` method signature is dense (three keyword arguments plus the event type).

## References

- §9.7 Saga `on()` DSL
