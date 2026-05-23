# Read Models

> **Adoption Level:** 2 — CQRS
> **Module:** `pydomain.cqrs.projection`

## What is a Read Model?

A **Read Model** is a query-optimized data structure derived from domain events. It answers specific queries efficiently without loading aggregates from the write side. Read models are **disposable** — they can always be rebuilt from the event log.

This is the core insight of CQRS: the write side (aggregates, commands) is optimized for consistency and invariants, while the read side (projections, queries) is optimized for query performance.

## The `Projection[StateT]` Protocol

```python
from pydomain.cqrs.projection import Projection


class Projection[StateT](Protocol):
    async def apply(self, event: DomainEvent) -> None:
        """Apply a domain event to this projection."""
        ...

    async def rebuild(self, events: Sequence[DomainEvent]) -> None:
        """Rebuild from scratch by replaying a full event stream."""
        ...
```

A projection follows the **left-fold** pattern: `current_state + event → new_state`.

## How Projections Work

```
Domain Events (event log)
  │
  ├── OrderPlaced
  ├── OrderLineAdded
  ├── OrderShipped
  └── ...
  │
  └── Projection.apply(event)
        └── Updates internal read model state
              │
              └── Persisted via ProjectionStore
```

Each event transforms the read model. The projection decides which events are relevant:

```python
class OrderSummaryProjection(Projection[OrderSummary]):
    def __init__(self) -> None:
        self._state: OrderSummary | None = None

    async def apply(self, event: DomainEvent) -> None:
        if isinstance(event, OrderPlaced):
            self._state = OrderSummary(
                order_id=event.order_id,
                customer_id=event.customer_id,
                total=event.total_amount,
            )
        elif isinstance(event, OrderShipped):
            if self._state:
                self._state.status = "shipped"
        # Irrelevant events are silently skipped

    async def rebuild(self, events: Sequence[DomainEvent]) -> None:
        self._state = None
        for event in events:
            await self.apply(event)
```

## The `ProjectionStore` Protocol

```python
from pydomain.cqrs.projection import ProjectionStore


class ProjectionStore(Protocol):
    async def load(self, projection_id: str) -> Any | None:
        """Load persisted state for a projection."""
        ...

    async def save(self, projection_id: str, state: Any) -> None:
        """Persist projection state."""
        ...
```

The store handles persistence of the opaque read model state. This is separate from checkpoint tracking (handled by `CheckpointStore` in the ES layer).

## Disposability

A key property of read models: they can be deleted and rebuilt from the event log at any time. This means:

- Schema changes are cheap — just rebuild
- Bugs in projection logic are easily fixed — rebuild from events
- New read models can be created for existing event streams

## Read Models vs Domain Entities

| Aspect | Domain Entity | Read Model |
|--------|-------------|------------|
| Purpose | Enforce invariants | Answer queries |
| Source | Created by commands | Derived from events |
| Mutability | Mutable (state changes) | Mutable (updated by events) |
| Disposability | Cannot be discarded | Can be rebuilt from events |
| Query optimization | Not optimized for queries | Denormalized, indexed |

## Projection Types

**Synchronous projections** update in the same transaction as the command. Good for read models that must be immediately consistent.

**Asynchronous projections** update via event subscriptions after the transaction commits. Good for read models that can tolerate eventual consistency. See [Event Sourcing](../es/event-sourcing.md) for subscription-based projections.

## Next Steps

- **[Define a Read Store Protocol →](../../how-to/cqrs/define-read-store-protocol.md)** — protocol design
- **[Implement a Read Store →](../../how-to/cqrs/implement-read-store.md)** — concrete storage
- **[Handle Domain Events →](../../how-to/cqrs/handle-domain-events.md)** — wiring projections to events
- **[Build Projections (Recipe) →](../../how-to/recipes/build-projections.md)** — end-to-end pattern
