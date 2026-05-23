# Query Bus

> **Adoption Level:** 2 — CQRS
> **Module:** `pydomain.cqrs.query_bus`

## What is the Query Bus?

The **Query Bus** routes queries to their single registered handler and returns a typed result. Unlike the [Command Bus](command-bus.md), queries are **read-only** — no Unit of Work, no events, no transaction boundary.

## Architecture

```
QueryBus.dispatch(query)
  │
  ├── 1. Look up handler + pipeline for query type
  ├── 2. Build MessageContext (query ID, kind=QUERY)
  ├── 3. Run pipeline (behaviors → terminal handler)
  └── 4. Return typed result directly
```

## Registration

Handlers are registered **per query type** with optional pipeline behaviors:

```python
from pydomain.cqrs.query_bus import QueryBus

bus = QueryBus()

bus.register(
    query_type=GetOrder,
    handler=GetOrderHandler(read_store),
    behaviors=[LoggingBehavior()],
)
```

Each query type can have exactly one handler. No UoW factory is needed — queries are read-only.

## Dispatch

`dispatch()` returns the typed result directly:

```python
result = await bus.dispatch(GetOrder(order_id=some_id))
# result: GetOrderResult — typed, no events tuple
```

Compare with the Command Bus, which returns `(result, events)`.

## No Unit of Work

The critical difference from the Command Bus: **no UoW**. Queries don't modify aggregates, so there's nothing to commit or roll back. The handler receives only the query:

```python
class GetOrderHandler:
    def __init__(self, read_store: OrderReadStore) -> None:
        self._read_store = read_store

    async def __call__(self, query: GetOrder) -> GetOrderResult:
        # No UoW parameter — read-only
        data = await self._read_store.get(query.order_id)
        return GetOrderResult(**data)
```

## Pipeline Behaviors

Like the Command Bus, the Query Bus supports pipeline behaviors. Common uses:

- `LoggingBehavior` — log query execution time
- `ValidationBehavior` — validate query parameters before execution

Behaviors are registered per query type at registration time.

## Commands vs Queries — Bus Comparison

| Aspect | CommandBus | QueryBus |
|--------|-----------|----------|
| Registration | `(command_type, handler, uow_factory, behaviors)` | `(query_type, handler, behaviors)` |
| Return type | `tuple[CommandResult, list[DomainEvent]]` | `QueryResult` |
| Unit of Work | Created from factory, managed by bus | None |
| Events | Collected and stamped | None |
| Tracing IDs | Propagated from command | Query ID only |
| Transaction | Commit on success, rollback on failure | N/A |

## Next Steps

- **[Configure the Query Bus →](../../how-to/cqrs/configure-query-bus.md)** — wiring guide
- **[Command Bus →](command-bus.md)** — the write-side counterpart
- **[Handlers →](handlers.md)** — handler protocols
- **[Pipeline Behaviors →](pipeline-behaviors.md)** — middleware for both buses
