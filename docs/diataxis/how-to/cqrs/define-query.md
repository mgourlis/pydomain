# How to Define a Query

> **Prerequisite:** [Queries concept](../../concepts/cqrs/queries.md)

## Problem

You need to ask the system for data — "get order details," "find active customers," "list pending shipments."

## Solution

Subclass `Query[TResult]` with the query parameters. Name it in nominative/descriptive mood. Bind the result type via the generic parameter.

## Steps

### 1. Define the result type

```python
from uuid import UUID
from datetime import datetime
from pydomain.cqrs.queries import QueryResult


class GetOrderResult(QueryResult):
    order_id: UUID
    customer_name: str
    total: int
    status: str
    items: list[OrderLineProjection]
    placed_at: datetime
```

### 2. Define the query

```python
from uuid import UUID
from pydomain.cqrs.queries import Query


class GetOrder(Query[GetOrderResult]):
    order_id: UUID
```

### 3. Use it

```python
query = GetOrder(order_id=some_id)

result = await query_bus.dispatch(query)
# result: GetOrderResult — typed
print(result.status)
```

## Conventions

**Name in nominative mood.** Queries describe what they return: `GetOrder`, `FindActiveCustomers`, `OrderHistory`. Not `FetchOrder` (vague), not `GetOrderQuery` (suffix is noise).

**Keep parameters minimal.** Only include what the handler needs to filter and project. Avoid bloated queries with optional everything.

**Results are DTOs.** Never return domain entities from a query. The result should be flat, serializable data.

## Filtering Queries

For queries with optional filters:

```python
from datetime import datetime


class FindOrdersResult(QueryResult):
    orders: list[OrderSummary]
    total_count: int


class FindOrders(Query[FindOrdersResult]):
    customer_id: UUID | None = None
    status: str | None = None
    since: datetime | None = None
    limit: int = 20
    offset: int = 0
```

## Multi-Parameter Queries

Group related parameters into nested models for readability:

```python
class DateRange(BaseModel):
    start: datetime
    end: datetime


class OrderHistory(Query[OrderHistoryResult]):
    customer_id: UUID
    date_range: DateRange
    include_cancelled: bool = False
```

## See Also

- [Queries concept](../../concepts/cqrs/queries.md)
- [Implement a Query Handler](implement-query-handler.md)
- [Command Result Types](command-result-types.md)
- [Define a Command](define-command.md)
