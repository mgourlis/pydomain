# How to Define a Command

> **Prerequisite:** [Commands concept](../../concepts/cqrs/commands.md)

## Problem

You need to express an intent to modify the system — "place an order," "cancel a subscription," "update a profile."

## Solution

Subclass `Command[TResult]` with the data the handler needs. Name it in imperative mood. Bind the result type via the generic parameter.

## Steps

### 1. Define the result type

```python
from uuid import UUID
from datetime import datetime
from pydomain.cqrs.commands import CommandResult


class PlaceOrderResult(CommandResult):
    order_id: UUID
    status: str
    placed_at: datetime
```

### 2. Define the command

```python
from uuid import UUID
from pydomain.cqrs.commands import Command


class PlaceOrder(Command[PlaceOrderResult]):
    customer_id: UUID
    items: list[OrderLine]
    shipping_address: Address
```

### 3. Use it

```python
cmd = PlaceOrder(
    customer_id=customer_id,
    items=[OrderLine(product_id=p1, quantity=2)],
    shipping_address=Address(street="123 Main", city="Athens"),
)

print(cmd.command_id)  # UUIDv7 — auto-generated
print(cmd.correlation_id)  # None — for saga use
```

## Conventions

**Name in imperative mood.** Commands are instructions: `PlaceOrder`, `CancelOrder`, `UpdateProfile`. Not `OrderPlaced` (that's an event), not `PlaceOrderCommand` (the suffix is noise).

**Carry all needed data.** The handler should not need to look up additional data. If the handler needs a `customer_id`, include it in the command.

**One command = one aggregate.** A command modifies exactly one aggregate. If you need to modify two aggregates, dispatch two commands (possibly from a saga).

## With Custom Fields

Commands can use Pydantic field validators for structural constraints:

```python
from pydantic import field_validator


class PlaceOrder(Command[PlaceOrderResult]):
    customer_id: UUID
    items: list[OrderLine]

    @field_validator("items")
    @classmethod
    def must_not_be_empty(cls, v: list[OrderLine]) -> list[OrderLine]:
        if not v:
            raise ValueError("Order must have at least one item")
        return v
```

## Fire-and-Forget Commands

Use `EmptyCommandResult` when no meaningful output is needed:

```python
from pydomain.cqrs.commands import EmptyCommandResult


class DeleteOrder(Command[EmptyCommandResult]):
    order_id: UUID
```

The handler returns `EmptyCommandResult()`. If the handler returns `None`, the bus wraps it automatically.

## Tracing for Sagas

When a saga dispatches a command, set explicit tracing IDs to maintain the correlation chain:

```python
cmd = NotifyCustomer(
    customer_id=event.customer_id,
    correlation_id=event.correlation_id,  # From the triggering event
    causation_id=event.event_id,          # This event caused this command
)
await bus.dispatch(cmd)
```

## See Also

- [Commands concept](../../concepts/cqrs/commands.md)
- [Implement a Command Handler](implement-command-handler.md)
- [Command Result Types](command-result-types.md)
- [Define a Query](define-query.md)
