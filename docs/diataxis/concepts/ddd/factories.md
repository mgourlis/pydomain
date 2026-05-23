# Factories

> **Adoption Level:** 1 — Tactical DDD
> **Module:** `pydomain.ddd.factory`

## What is a Factory?

A **Factory** encapsulates the creation and reconstitution of complex domain objects. It hides construction details from clients, ensuring that every created object is in a valid state.

pydomain provides two factory protocols:

| Protocol | Method | Purpose |
|----------|--------|---------|
| `Factory[T]` | `create()` | Build a **new** domain object |
| `ReconstitutionFactory[T]` | `reconstitute()` | Rebuild from **persisted** state |

Both are `typing.Protocol` — any class with the matching method structurally conforms. No base class inheritance required.

## `Factory[T]` — Creation

The `Factory[T]` protocol is for building new domain objects with fresh identities:

```python
@runtime_checkable
class Factory[T](Protocol):
    def create(self, *args: Any, **kwargs: Any) -> T: ...
```

Any class with a `create()` method returning `T` conforms. Explicit inheritance is recommended for clarity:

```python
class OrderFactory(Factory[Order]):
    def __init__(self, pricing: PricingService) -> None:
        self._pricing = pricing

    def create(self, customer_id: UUID, *, items: list[OrderItem]) -> Order:
        total = self._pricing.calculate_total(items)
        order = Order(customer_id=customer_id, total=total)
        order.apply_discount_if_eligible()
        return order
```

Two invariants hold for every factory:

1. **Atomicity** — each `create` call is all-or-nothing: either returns a valid object or raises
2. **Abstracted type** — the caller asks for the domain type, never for internal details

## `ReconstitutionFactory[T]` — Reconstitution

The `ReconstitutionFactory[T]` protocol is for **rebuilding** domain objects from persisted state:

```python
@runtime_checkable
class ReconstitutionFactory[T](Protocol):
    def reconstitute(self, *args: Any, **kwargs: Any) -> T: ...
```

```python
class OrderReconstitutor(ReconstitutionFactory[Order]):
    def reconstitute(self, id: UUID, customer_id: UUID, items: list[dict]) -> Order:
        order = Order(id=id, customer_id=customer_id, items=[...])
        return order
```

> ⚠️ **Reconstitution must NEVER generate a new identity.** The `id` comes from the persisted data, not from the `IdGenerator`. This is why `reconstitute()` is a separate method from `create()` — to prevent accidentally mixing creation and rebuilding logic.

## Three Factory Patterns

### 1. Standalone Factory

A dedicated class implementing `Factory[T]`:

```python
class OrderFactory(Factory[Order]):
    def create(self, customer_id: UUID, items: list[OrderItem]) -> Order:
        ...
```

Use when construction requires injected dependencies (pricing services, policy checks).

### 2. Factory Method on Aggregate Root

The aggregate root itself acts as a factory for child entities:

```python
class Order(AggregateRoot[UUID]):
    items: list[OrderItem] = []

    def add_line_item(self, product: str, quantity: int, price: int) -> None:
        item = OrderItem(product=product, quantity=quantity, price=price)
        self.items.append(item)
```

No separate factory class needed — the aggregate manages its own children.

### 3. Reconstitution Factory

For event-sourced aggregates and repository reads:

```python
class OrderReconstitutor(ReconstitutionFactory[Order]):
    def reconstitute(self, id: UUID, customer_id: UUID, ...) -> Order:
        ...
```

Separate from creation to enforce the "never generate new ID" invariant.

## When to Use a Factory

Use a Factory when:

- Construction logic is **complex** (dependencies, multi-step validation, defaults)
- You need to **centralize** creation rules that would otherwise be duplicated
- Construction requires **injected services** (pricing, inventory checks, policy engines)

Avoid when:

- A simple constructor call is sufficient (`Order(customer_id=..., total=...)`)
- There's only one place that creates the object

## Next Steps

- **[Implement a Factory →](../../how-to/ddd/implement-factory.md)** — step-by-step guide
- **[Entity Identity →](entity-identity.md)** — why reconstitution must preserve identity
- **[Repositories →](repositories.md)** — where reconstitution factories are used
