# Command & Query Result Types

> **Adoption Level:** 2 — CQRS
> **Module:** `pydomain.cqrs.commands`, `pydomain.cqrs.queries`

## What are Result Types?

Result types provide **compile-time and runtime type safety** for the CQRS message pipeline. Every command and query declares exactly what its handler returns, making `dispatch()` return types explicit rather than `Any`.

## The Type Hierarchy

```
CommandResult          QueryResult
    │                      │
    ├── PlaceOrderResult   ├── GetOrderResult
    ├── CancelOrderResult  ├── ActiveCustomersResult
    └── EmptyCommandResult └── ...
```

`CommandResult` and `QueryResult` are abstract base classes. Every concrete command or query defines its own result type as a subclass.

## `CommandResult`

```python
from pydantic import BaseModel, ConfigDict
from pydomain.cqrs.commands import CommandResult


class CommandResult(BaseModel):
    """Abstract base for command results."""
    model_config = ConfigDict(frozen=True)
```

Results are frozen Pydantic models. They can carry any data the caller needs after the command completes:

```python
class PlaceOrderResult(CommandResult):
    order_id: UUID
    status: str
    placed_at: datetime
```

## `EmptyCommandResult`

For commands that produce no meaningful output (the equivalent of `void`):

```python
from pydomain.cqrs.commands import EmptyCommandResult


class DeleteOrder(Command[EmptyCommandResult]):
    order_id: UUID

# Handler
async def __call__(self, cmd: DeleteOrder, uow: UnitOfWork) -> EmptyCommandResult:
    order = await uow.orders.get_by_id(cmd.order_id)
    order.delete()
    return EmptyCommandResult()
```

If a handler returns `None`, the Command Bus automatically wraps it as `EmptyCommandResult()`.

## `QueryResult`

```python
from pydomain.cqrs.queries import QueryResult


class QueryResult(BaseModel):
    """Abstract base for query results."""
    model_config = ConfigDict(frozen=True)
```

Query results are also frozen Pydantic models. They carry the projected data the caller requested:

```python
class GetOrderResult(QueryResult):
    order_id: UUID
    customer_name: str
    total: int
    status: str
    items: list[OrderLineProjection]
```

## Type Binding

The generic type parameter on `Command[TResult]` and `Query[TResult]` binds the message to its result type:

```python
class PlaceOrder(Command[PlaceOrderResult]):
    order_id: UUID
    items: list[OrderLine]

class GetOrder(Query[GetOrderResult]):
    order_id: UUID
```

This binding is enforced by the [Command Bus](command-bus.md) and [Query Bus](query-bus.md) at dispatch time, providing IDE autocompletion and mypy/pyright type checking.

## Result as DTO

Result types are DTOs (Data Transfer Objects) — they carry data across the application boundary, not domain logic. Keep them flat and serializable:

```python
# Good — flat DTO with primitives
class PlaceOrderResult(CommandResult):
    order_id: UUID
    status: str
    placed_at: datetime

# Avoid — nesting domain objects
class PlaceOrderResult(CommandResult):
    order: Order  # Don't leak domain objects into results
```

## Designing Results

- **Commands** return results that confirm what happened: new IDs, status changes, timestamps
- **Queries** return results that project data for the caller: denormalized views, lists, summaries
- **Empty results** use `EmptyCommandResult` for fire-and-forget commands
- **Never return domain entities** — results are DTOs, not domain objects

## Next Steps

- **[Command Result Types →](../../how-to/cqrs/command-result-types.md)** — how-to guide with examples
- **[Commands →](commands.md)** — the command base class
- **[Queries →](queries.md)** — the query base class
- **[Handlers →](handlers.md)** — returning results from handlers
