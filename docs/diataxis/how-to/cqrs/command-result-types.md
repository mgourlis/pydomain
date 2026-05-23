# How to Define Command & Query Result Types

> **Prerequisite:** [Command & Query Result Types concept](../../concepts/cqrs/command-query-result-types.md)

## Problem

You need to define what data a command handler or query handler returns, with proper typing so callers get IDE autocompletion and type safety.

## Solution

Subclass `CommandResult` or `QueryResult` and declare the fields the caller needs.

## Steps

### 1. Choose the base class

```python
from pydomain.cqrs.commands import CommandResult
from pydomain.cqrs.queries import QueryResult
```

Use `CommandResult` for command return types, `QueryResult` for query return types.

### 2. Declare the fields

```python
from uuid import UUID
from datetime import datetime


class PlaceOrderResult(CommandResult):
    order_id: UUID
    status: str
    placed_at: datetime
    total_amount: int
```

Keep it flat and serializable. These are DTOs, not domain objects.

### 3. Bind to the message

```python
from pydomain.cqrs.commands import Command


class PlaceOrder(Command[PlaceOrderResult]):
    customer_id: UUID
    items: list[OrderLine]
```

The generic parameter `Command[PlaceOrderResult]` binds the command to its result type.

## Command Results

Command results confirm what happened:

```python
class CancelOrderResult(CommandResult):
    order_id: UUID
    cancelled_at: datetime
    refund_amount: int


class UpdateProfileResult(CommandResult):
    user_id: UUID
    fields_updated: list[str]
```

Include actionable data the caller needs: new IDs, timestamps, status changes. Don't return the full aggregate.

## Query Results

Query results project data for the caller:

```python
class OrderSummary(BaseModel):
    order_id: UUID
    total: int
    status: str


class FindOrdersResult(QueryResult):
    orders: list[OrderSummary]
    total_count: int
```

Query results can contain lists, nested DTOs, and computed values. They're read-only views.

## Empty Results

For fire-and-forget commands, use `EmptyCommandResult`:

```python
from pydomain.cqrs.commands import EmptyCommandResult


class ArchiveOrder(Command[EmptyCommandResult]):
    order_id: UUID

# In the handler:
async def __call__(self, cmd: ArchiveOrder, uow: UnitOfWork) -> EmptyCommandResult:
    order = await uow.orders.get_by_id(cmd.order_id)
    order.archive()
    return EmptyCommandResult()
```

## Result as DTO — Dos and Don'ts

```python
# Do: flat DTO with primitives
class PlaceOrderResult(CommandResult):
    order_id: UUID
    status: str
    placed_at: datetime

# Don't: leak domain objects
class PlaceOrderResult(CommandResult):
    order: Order  # No — domain object in result

# Don't: include handler internals
class PlaceOrderResult(CommandResult):
    db_session: Any  # No — infrastructure leak
```

## Typed Dispatch

The type binding pays off at the call site:

```python
# mypy/pyright knows the type
result, events = await bus.dispatch(PlaceOrder(...))
# result: PlaceOrderResult

print(result.order_id)  # Autocompleted, type-safe
```

## See Also

- [Command & Query Result Types concept](../../concepts/cqrs/command-query-result-types.md)
- [Define a Command](define-command.md)
- [Define a Query](define-query.md)
- [Implement a Command Handler](implement-command-handler.md)
