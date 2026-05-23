# Aggregates

> **Adoption Level:** 1 — Tactical DDD
> **Module:** `pydomain.ddd.aggregate_root`
> **Prerequisites:** [Entities](entities.md), [Domain Events](domain-events.md)

## What is an Aggregate?

An **Aggregate** is a cluster of domain objects treated as a single unit for data changes. The **Aggregate Root** is the entry point — the only object that external code can hold a reference to.

Every Aggregate Root:

1. **Enforces invariants** — business rules that must hold after every mutation
2. **Owns domain events** — records facts about what happened
3. **Has a Repository** — only Aggregate Roots get persistence contracts

## The `AggregateRoot[TId]` Base Class

```python
class AggregateRoot[TId](Entity[TId]):
    _pending_events: list[DomainEvent] = PrivateAttr(default_factory=list)
```

`AggregateRoot` extends `Entity[TId]` with domain event management:

| Feature | From `Entity[TId]` | Added by `AggregateRoot` |
|---------|--------------------|--------------------------|
| Identity (`id`) | ✅ | — |
| Version (`version`) | ✅ | — |
| Equality by identity | ✅ | — |
| Pending events | — | ✅ |
| `_add_event()` | — | ✅ |
| `pull_events()` | — | ✅ |

## Consistency Boundary

The Aggregate Root is the **consistency boundary** — all mutations go through it:

```python
from pydomain.ddd.exceptions import DomainError


class OrderNotModifiable(DomainError):
    """Raised when a placed order cannot be modified."""


class OrderNotPlacable(DomainError):
    """Raised when an order cannot be placed."""


class Order(AggregateRoot[UUID]):
    customer_id: UUID
    total: Money
    status: str = "pending"
    items: list[OrderItem] = []

    def add_item(self, item: OrderItem) -> None:
        """Add an item — enforces invariants."""
        if self.status != "pending":
            raise OrderNotModifiable("Cannot modify a placed order")
        self.items.append(item)
        self.total = self.total.add(item.price)

    def place(self) -> None:
        """Place the order — transitions status and records event."""
        if self.status != "pending":
            raise OrderNotPlacable("Order is not pending")
        if not self.items:
            raise OrderNotPlacable("Cannot place an empty order")

        self.status = "placed"
        self._add_event(OrderPlaced(
            order_id=self.id,
            total_amount=self.total.amount,
            currency=self.total.currency,
        ))
```

Key points:
- **No external mutation** — only the aggregate's own methods change its state
- **Invariants hold after every mutation** — `place()` checks status and items before proceeding
- **Events are recorded, not published** — `_add_event()` buffers; the Unit of Work publishes

## Enforcing Invariants

Aggregate roots enforce invariants through a **three-tier validation approach**:

1. **Pydantic validators** (`@field_validator`) — structural constraints that are always true (e.g., "price cannot be negative"). Run at construction time with `ValueError`.

2. **Domain exceptions** (`DomainError` subclasses) — business rules that depend on state (e.g., "only draft orders can be submitted"). Raised in mutation methods and named in the Ubiquitous Language.

3. **Specifications** — reusable, composable business rules that can be shared across validation, querying, and generation contexts.

> **Rule of thumb:** If the rule is about *what* a valid value looks like, use Pydantic. If it's about *when* something can happen, use a `DomainError`.

See [Define an Aggregate →](../../how-to/ddd/define-aggregate.md) for the full guide with examples.

## Event Lifecycle

```
┌─────────────────────┐
│ Aggregate Method     │
│ (e.g., place())     │
│                     │
│ 1. Check invariants │
│ 2. Mutate state     │
│ 3. _add_event()     │──→ _pending_events buffer
└─────────────────────┘

                      ┌──────────────────┐
                      │ Unit of Work     │
                      │                  │
                      │ 4. commit()      │
                      │ 5. pull_events() │──→ drain buffer
                      │ 6. stamp()       │──→ add tracing IDs
                      │ 7. publish       │──→ MessageBus
                      └──────────────────┘
```

### `_add_event(event)`

Records a domain event in the internal buffer. Called by aggregate methods.

### `pull_events()`

Returns all buffered events and clears the buffer. Called by the Unit of Work after `commit()`.

```python
events = order.pull_events()  # Returns [OrderPlaced(...)]
events = order.pull_events()  # Returns [] — buffer was cleared
```

## Only Aggregate Roots Have Repositories

This is a key DDD rule: **exactly one Repository per Aggregate Root type**. Internal entities within the aggregate are loaded and saved together through the root.

```python
# ✅ Correct — repository for the aggregate root
class OrderRepository(Repository[Order, UUID]): ...

# ❌ Wrong — no repository for internal entities
class OrderItemRepository(Repository[OrderItem, UUID]): ...  # Don't do this
```

## Publish-After-Commit Semantics

Events are **not published** during command handling. They are published **after** the Unit of Work successfully commits. This guarantees:

1. **Atomicity** — if the commit fails, no events are published
2. **Consistency** — handlers see events only after the state is persisted
3. **Traceability** — `correlation_id` and `causation_id` are stamped before publishing

## Private Attributes and Frozen Models

`_pending_events` uses Pydantic's `PrivateAttr` because `AggregateRoot` inherits from `Entity` which has `frozen=False`. The private attribute is a mutable list — the list itself is mutated (append, clear), but the model's public fields follow entity semantics.

## Next Steps

- **[Define an Aggregate →](../../how-to/ddd/define-aggregate.md)** — step-by-step guide
- **[Repositories →](repositories.md)** — persistence contracts for aggregates
- **[Domain Events →](domain-events.md)** — what aggregates record
