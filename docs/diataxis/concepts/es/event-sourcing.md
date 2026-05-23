# Event Sourcing

> **Adoption Level:** 4 — Event Sourcing
> **Module:** `pydomain.es`
> **Prerequisites:** [Aggregates](../ddd/aggregates.md), [Domain Events](../ddd/domain-events.md), [Commands](../cqrs/commands.md), [Command Bus](../cqrs/command-bus.md)

## What is Event Sourcing?

**Event Sourcing** stores the state of an aggregate not as a current snapshot row, but as a sequence of immutable domain events that, when replayed, reconstruct the aggregate's current state.

Instead of saving "Order #42 is Placed with total $100," you save:

```
OrderCreated(order_id=42, customer_id=7)
LineItemAdded(order_id=42, product_id=88, qty=2, price=30)
LineItemAdded(order_id=42, product_id=91, qty=1, price=40)
OrderPlaced(order_id=42, placed_at=...)
```

To get the current state, replay the events: create an empty Order, apply each event in order. The final aggregate equals the one that produced those events.

## Why Event Sourcing?

| Benefit | How |
|---------|-----|
| **Full audit trail** | Every state change is recorded as an event — you can answer "how did we get here?" |
| **Temporal queries** | Reconstruct state at any point in time by replaying events up to that point |
| **Schema evolution** | Old events remain immutable; upcasters transform them on read |
| **Read model flexibility** | Build any projection by replaying events — change your query model without touching write-side code |
| **Debugging & replay** | Replay production events in a test environment to reproduce bugs exactly |

## Event-Sourced Aggregate Lifecycle

An event-sourced aggregate differs from a classic DDD aggregate in how state mutation works:

```
┌─────────────────────────────────────────────────┐
│ COMMAND                                          │
│ PlaceOrder(order_id=42)                          │
└─────────────────┬───────────────────────────────┘
                  ▼
┌─────────────────────────────────────────────────┐
│ HANDLER                                          │
│ 1. Load aggregate (replay events)                │
│ 2. Call aggregate.place()                        │
│ 3. Save aggregate (append new events)            │
└─────────────────┬───────────────────────────────┘
                  ▼
┌─────────────────────────────────────────────────┐
│ AGGREGATE — _apply(event)                        │
│ 1. _when(event) → mutate state                   │
│ 2. _add_event(event) → buffer                    │
│ 3. version += 1                                  │
└─────────────────┬───────────────────────────────┘
                  ▼
┌─────────────────────────────────────────────────┐
│ REPOSITORY — save(aggregate)                     │
│ 1. pull_events() → drain buffer                  │
│ 2. event_store.append_to_stream(...)             │
│    with optimistic concurrency check             │
│ 3. Optionally take snapshot                      │
└─────────────────────────────────────────────────┘
```

## Event Stream vs. Current State

In a DDD-only (Level 1) application, the repository directly persists the aggregate's current state:

```python
# DDD-only: save current state
await repository.save(aggregate)  # UPDATE orders SET status='placed' WHERE id=42
```

In event sourcing, the repository appends new events and reconstructs from the full stream:

```python
# ES: append events, rebuild from stream
await repository.save(aggregate)  # INSERT INTO events (aggregate_id, data) VALUES (42, 'OrderPlaced')
```

## Relationship to CQRS

Event Sourcing is a natural fit with CQRS:

- **Commands** produce events (the write side)
- **Events** are the source of truth
- **Projections** consume events to build read models (the read side)

See [CQRS with DDD recipe](../../how-to/recipes/cqrs-with-ddd.md) and [ES with CQRS recipe](../../how-to/recipes/es-with-cqrs.md).

## Common pitfalls

> **⚠️** **Don't mix `_apply` and direct mutation.** Event-sourced aggregates must route all state changes through `_apply(event)` → `_when(event)`. Direct field assignment (`self.status = "placed"`) bypasses event recording and breaks the audit trail.

> **⚠️** **Snapshots are optional, not required.** You can start with event-only persistence and add snapshots later when replay performance becomes an issue. Don't over-engineer snapshot policies upfront.

## Next steps

- [Event-Sourced Aggregates](event-sourced-aggregates.md) — the aggregate base class
- [Event Store](event-store.md) — the persistence protocol
- [Event-Sourced Repositories](event-sourced-repositories.md) — how repositories load and save
