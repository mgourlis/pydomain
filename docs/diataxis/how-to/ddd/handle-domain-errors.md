# How to Handle Domain Errors

> **Prerequisite:** [Exception Hierarchy](../../concepts/infrastructure/exception-hierarchy.md)

## Problem

You need to handle domain-layer errors (business rule violations, concurrency conflicts) in a structured way.

## Solution

Catch `DomainError` and its subclasses. Each error type has a specific handling strategy.

## Error Types and Strategies

### `DomainError` — Catch-All

The base for all domain errors. Catch this when you want uniform handling:

```python
from pydomain.ddd.exceptions import DomainError

try:
    order.place()
except DomainError as e:
    logger.warning(f"Domain error: {e}")
    return ErrorResponse(message=str(e))
```

### `ConcurrencyError` — Retry

Raised when the aggregate version changed between load and save:

```python
from pydomain.ddd.exceptions import ConcurrencyError

async def handle_with_retry(command: PlaceOrder, repo: OrderRepository) -> Order:
    max_retries = 3
    for attempt in range(max_retries):
        try:
            order = await repo.get_by_id(command.order_id)
            if order is None:
                raise ValueError("Order not found")
            order.place()
            await repo.save(order)
            return order
        except ConcurrencyError:
            if attempt == max_retries - 1:
                raise
            continue
    raise RuntimeError("Max retries exceeded")
```

### `SpecificationError` — Validation Feedback

Raised when a specification-based validation rule fails:

```python
from pydomain.ddd.exceptions import SpecificationError

try:
    eligible = IsEligibleForDiscount(min_total=1000)
    if not eligible.is_satisfied_by(order):
        raise SpecificationError("Order does not meet discount requirements")
except SpecificationError as e:
    return ValidationResponse(errors=[str(e)])
```

## Layer-Specific Handling

### In Aggregate Methods — Raise

Aggregates raise exceptions for precondition failures:

```python
class Order(AggregateRoot[UUID]):
    def place(self) -> None:
        if self.status != "pending":
            raise ValueError("Order is not pending")
        self.status = "placed"
        self._add_event(OrderPlaced(...))
```

### In Command Handlers — Let It Propagate

Command handlers don't catch domain errors — they propagate to the command bus:

```python
async def handle(command: PlaceOrder, uow: UnitOfWork) -> OrderResult:
    order = await uow.repository.get_by_id(command.order_id)
    order.place()  # May raise ValueError
    await uow.repository.save(order)
    return OrderResult(order_id=order.id)
```

### At the API Boundary — Translate

Convert domain errors to user-friendly responses at the edge:

```python
from fastapi import HTTPException
from pydomain.ddd.exceptions import ConcurrencyError, DomainError

@app.post("/orders/{order_id}/place")
async def place_order(order_id: UUID):
    try:
        result = await command_bus.dispatch(PlaceOrder(order_id=order_id))
        return result
    except ConcurrencyError:
        raise HTTPException(status_code=409, detail="Conflict — order was modified by another request")
    except DomainError as e:
        raise HTTPException(status_code=422, detail=str(e))
```

## Design Rules

- **Don't catch domain errors inside aggregates** — let them propagate
- **Don't raise exceptions for domain events** — events and exceptions serve different purposes
- **Don't leak domain exceptions through APIs** — translate at the boundary
- **Don't create new exception types** unless callers need to handle them differently

## See Also

- [Exception Hierarchy](../../concepts/infrastructure/exception-hierarchy.md)
- [Specifications](../../concepts/ddd/specifications.md) — where `SpecificationError` originates
- [Repositories](../../concepts/ddd/repositories.md) — where `ConcurrencyError` originates
