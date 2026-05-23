# Queries

> **Adoption Level:** 2 — CQRS
> **Module:** `pydomain.cqrs.queries`

## What is a Query?

A **Query** asks a question — "give me this data." It carries all parameters the handler needs to fetch and project data. Queries are **read-only**: no side effects, no aggregate mutation, no Unit of Work.

Queries are named in **nominative/descriptive mood**: `GetOrder`, `FindActiveCustomers`, `OrderHistory`.

## The `Query[TResult]` Base Class

```python
from uuid import UUID
from pydantic import Field
from pydomain.cqrs.queries import Query, QueryResult


class Query[TResult: QueryResult](BaseModel):
    query_id: UUID = Field(default_factory=...)

    model_config = ConfigDict(frozen=True, extra="forbid")
```

| Field | Type | Purpose |
|-------|------|---------|
| `query_id` | `UUID` | Unique query identifier (UUIDv7, auto-generated) |

The generic type parameter `TResult` binds the query to its expected result type.

## Read-Only by Contract

Queries have no `UnitOfWork`, no event collection, and no tracing IDs. The [Query Bus](query-bus.md) dispatches them with a single `dispatch()` call that returns the typed result directly:

```python
result = await query_bus.dispatch(GetOrder(order_id=some_id))
# result is typed as GetOrderResult
```

This read-only contract is enforced by the Query Bus — there is no UoW parameter in the handler signature, and no events are collected after dispatch.

## Nominative Naming

Queries describe what data they return:

```python
# Correct — descriptive
class GetOrder(Query[GetOrderResult]):
    order_id: UUID

class FindActiveCustomers(Query[ActiveCustomersResult]):
    since: datetime

# Wrong — imperative (that's a command)
class FetchOrder(Query[GetOrderResult]): ...
```

## Frozen and Explicit

Like commands, queries are frozen and forbid extra fields:

```python
class GetOrder(Query[GetOrderResult]):
    order_id: UUID

# Raises ValidationError — frozen
q = GetOrder(order_id=some_id)
q.order_id = other_id  # Error
```

## Query ID Generation

Queries auto-generate a UUIDv7 `query_id`. Configure a different generator at startup:

```python
from pydomain.cqrs.queries import Query

Query.configure(id_generator=Uuid7Generator())
```

## Relationship to QueryResult

Every `Query` has a bound `QueryResult` type. The handler returns this type, and `QueryBus.dispatch()` returns it typed. See [Command & Query Result Types](command-query-result-types.md) for details.

## Commands vs Queries

| Aspect | Command | Query |
|--------|---------|-------|
| Intent | "Do this" | "Give me this" |
| Naming | Imperative (`PlaceOrder`) | Nominative (`GetOrder`) |
| Side effects | Modifies one aggregate | Read-only |
| Unit of Work | Yes | No |
| Events | Collected and published | None |
| Tracing IDs | `correlation_id`, `causation_id` | None |

## Next Steps

- **[Define a Query →](../../how-to/cqrs/define-query.md)** — step-by-step guide
- **[Query Handlers →](handlers.md)** — the handler protocol
- **[Query Bus →](query-bus.md)** — routing and dispatch
- **[Commands →](commands.md)** — the write-side counterpart
