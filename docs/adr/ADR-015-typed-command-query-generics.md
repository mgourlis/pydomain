# ADR-015: Typed `Command[TResult]` / `Query[TResult]` with Generic Result Binding

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Without generic result binding, `dispatch()` returns `Any` — the caller has no type-safe way to know what a command or query returns. This forces downstream code to cast or guess, losing the benefit of static type checking.

In CQRS, each command type has a specific result type. `PlaceOrder` returns `PlaceOrderResult`. `CancelOrder` returns `EmptyCommandResult`. The dispatch return type must be tied to the command/query type at definition time.

## Decision

PEP 695 generics bind the result type at class definition time:

```python
class CommandResult(BaseModel):
    model_config = ConfigDict(frozen=True)

class EmptyCommandResult(CommandResult):
    """Void-style result for commands that produce no meaningful output."""

class Command[TResult: CommandResult](BaseModel):
    command_id: UUID
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    model_config = ConfigDict(frozen=True, extra="forbid")
```

Similarly for queries:

```python
class QueryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

class Query[TResult: QueryResult](BaseModel):
    query_id: UUID
    model_config = ConfigDict(frozen=True, extra="forbid")
```

Usage:

```python
class PlaceOrderResult(CommandResult):
    order_id: UUID

class PlaceOrder(Command[PlaceOrderResult]):
    customer_id: UUID
    items: list[OrderLine]
```

`EmptyCommandResult` serves as the void-equivalent for commands that return nothing meaningful.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| `dispatch()` returns `Any` | No type safety; callers must cast; defeats the purpose of typed commands |
| `dispatch()` returns `CommandResult` | Must downcast on every call; no compile-time guarantee that the result matches the command |
| Separate `dispatch_command()` and `dispatch_query()` with overloads | Complex overloads; hard to maintain; PEP 695 generics are simpler |
| Non-generic commands with `result_type` class variable | Runtime-only type checking; no static type safety |

## Consequences

### Positive

- `dispatch()` return types are explicit and type-safe at definition time.
- `EmptyCommandResult` provides a clear void-equivalent — no `None` ambiguity.
- Result types are frozen Pydantic models — immutable, serializable, validated.
- PEP 695 syntax is concise and idiomatic for Python 3.12+.

### Negative

- Requires Python 3.12+ (PEP 695 type parameter syntax).
- `CommandBus.dispatch()` returns `tuple[CommandResult, list[DomainEvent]]` — the result must still be downcast by the caller when the concrete type is needed.

### Neutral

- The `CommandResult` / `QueryResult` base classes add one level of inheritance but enable type-safe dispatch.

## References

- `src/pydomain/cqrs/commands.py` — `Command[TResult]`, `CommandResult`, `EmptyCommandResult`
- `src/pydomain/cqrs/queries.py` — `Query[TResult]`, `QueryResult`
- `src/pydomain/cqrs/command_bus.py` — `CommandBus.dispatch()` returns `tuple[CommandResult, list[DomainEvent]]`
- `src/pydomain/cqrs/query_bus.py` — `QueryBus.dispatch()` returns typed result
