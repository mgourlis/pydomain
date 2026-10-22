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

def place_or_report(order: Order) -> ErrorResponse | None:
    try:
        order.place()
    except DomainError as e:
        logger.warning(f"Domain error: {e}")
        return ErrorResponse(message=str(e))
    return None
```

### `ConcurrencyError` — Retry

Raised by a repository when the aggregate's version changed between load and save. The command bus does not pass it on as it is: a handler that raises *any* exception reaches the caller as a `CommandExecutionError` whose `__cause__` is the original (see [Exception Hierarchy](../../concepts/infrastructure/exception-hierarchy.md)). Retry at the call site that dispatches the command, so each attempt gets a fresh unit of work:

```python
from pydomain.cqrs import CommandExecutionError
from pydomain.ddd.exceptions import ConcurrencyError

async def dispatch_with_retry(command_bus, command, max_retries: int = 3):
    for attempt in range(max_retries):
        try:
            return await command_bus.dispatch(command)
        except CommandExecutionError as error:
            if isinstance(error.__cause__, ConcurrencyError) and attempt < max_retries - 1:
                continue
            raise
```

### `SpecificationError` — Your Own Validation Feedback

`SpecificationError` is a `DomainError` that the library defines for you to raise; no pydomain code raises it. Use it, or your own subclass, when a specification-based rule fails:

```python
from pydomain.ddd.exceptions import SpecificationError

def check_discount(order: Order) -> ValidationResponse | None:
    try:
        eligible = IsEligibleForDiscount(min_total=1000)
        if not eligible.is_satisfied_by(order):
            raise SpecificationError("Order does not meet discount requirements")
    except SpecificationError as e:
        return ValidationResponse(errors=[str(e)])
    return None
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

Command handlers don't catch domain errors — they propagate to the command bus, which raises them to the caller as a `CommandExecutionError` with the original as `__cause__`:

```python
async def handle(command: PlaceOrder, uow: UnitOfWork) -> OrderResult:
    order = await uow.repository.get_by_id(command.order_id)
    order.place()  # May raise ValueError; the bus wraps it in CommandExecutionError
    await uow.repository.save(order)
    return OrderResult(order_id=order.id)
```

### At the API Boundary — Translate

Convert domain errors to user-friendly responses at the edge:

```python
from fastapi import HTTPException
from pydomain.cqrs import CommandExecutionError
from pydomain.ddd.exceptions import ConcurrencyError

@app.post("/orders/{order_id}/place")
async def place_order(order_id: UUID):
    try:
        result = await command_bus.dispatch(PlaceOrder(order_id=order_id))
        return result
    except CommandExecutionError as error:
        if isinstance(error.__cause__, ConcurrencyError):
            raise HTTPException(status_code=409, detail="Conflict — order was modified by another request")
        raise HTTPException(status_code=422, detail=str(error.__cause__ or error))
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
