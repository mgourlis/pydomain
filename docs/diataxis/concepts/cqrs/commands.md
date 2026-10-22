# Commands

> **Adoption Level:** 2 — CQRS
> **Module:** `pydomain.cqrs.commands`

## What is a Command?

A **Command** expresses intent — "do this." It carries all the data a handler needs to perform a single operation. A command normally modifies one aggregate; when two aggregates must change together, one command can save both in one unit of work (see [Domain Services](../ddd/domain-services.md)).

Commands are named in **imperative mood** in the Ubiquitous Language: `PlaceOrder`, not `OrderPlaced`.

This is the fundamental distinction from [Domain Events](../ddd/domain-events.md): a Command is a request to do something, while a Domain Event is a record that something has already happened.

## The `Command[TResult]` Base Class

```python
from uuid import UUID
from pydantic import Field
from pydomain.cqrs.commands import Command, CommandResult


class Command[TResult: CommandResult](BaseModel):
    command_id: UUID = Field(default_factory=...)
    correlation_id: UUID | None = None
    causation_id: UUID | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")
```

| Field | Type | Purpose |
|-------|------|---------|
| `command_id` | `UUID` | Unique command identifier (UUIDv7, auto-generated) |
| `correlation_id` | `UUID \| None` | Distributed tracing — links events in the same workflow |
| `causation_id` | `UUID \| None` | Distributed tracing — which command caused this one |

The generic type parameter `TResult` binds the command to its expected result type, documenting the exact result type the handler produces. `dispatch()` itself is typed to return the base `CommandResult`, so callers narrow the result (see [Result Types](command-query-result-types.md)).

## Imperative Naming

Commands are named as instructions in the Ubiquitous Language:

```python
# Correct — imperative mood
class PlaceOrder(Command[PlaceOrderResult]):
    order_id: UUID
    customer_id: UUID
    items: list[OrderLine]

# Wrong — past tense (that's an event)
class OrderPlaced(Command[PlaceOrderResult]): ...
```

## Frozen and Explicit

Commands are frozen (`frozen=True`) and forbid extra fields (`extra="forbid"`). This ensures commands are immutable and self-contained — all data the handler needs must be declared explicitly:

```python
class CancelOrder(Command[CancelOrderResult]):
    order_id: UUID
    reason: str

# This raises ValidationError — frozen
cmd = CancelOrder(order_id=some_id, reason="customer request")
cmd.reason = "changed mind"  # Error

# This raises ValidationError — extra field
cmd = CancelOrder(order_id=some_id, reason="...", extra_field=42)  # Error
```

## Tracing IDs

The `correlation_id` and `causation_id` fields are optional. When `None` (the default for direct dispatches), the [Command Bus](command-bus.md) uses `command_id` as the fallback for both tracing IDs.

When a [Saga](../sagas/saga.md) dispatches a command, it sets explicit tracing IDs to maintain the correlation chain across multiple commands in the same workflow.

## Command ID Generation

Commands auto-generate a UUIDv7 `command_id` by default. You can configure a different generator at startup:

```python
from pydomain.cqrs.commands import Command

Command.configure(id_generator=Uuid7Generator())
```

`Command.configure()` sets the default for every command without its own generator; called on a command
class, it sets that class and its subclasses only ([ADR-078](../../../adr/ADR-078-cleanup-errors-stored-versions-and-per-class-configure.md)). A command subclass can also set its own
`_id_generator` class variable. When the command is validated (the
constructor, `model_validate()`, `model_validate_json()`), that subclass takes its `command_id` from it, as
entities, events and queries do; `model_construct()` skips validation and
uses the base class's generator ([ADR-070](../../../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md)):

```python
from typing import ClassVar
from uuid import UUID

from pydomain.cqrs.commands import Command, EmptyCommandResult
from pydomain.ddd.id_generator import IdGenerator


class FixedIdGenerator:
    def generate(self) -> UUID:
        return UUID("00000000-0000-4000-8000-0000000000c1")


class DispatchShipment(Command[EmptyCommandResult]):
    _id_generator: ClassVar[IdGenerator[UUID]] = FixedIdGenerator()
    shipment_id: UUID
```

The `command_id` is used by the [Idempotency Behavior](idempotency-and-locking.md) to detect duplicate commands.

## Relationship to CommandResult

Every `Command` has a bound `CommandResult` type. The handler returns this type, and `CommandBus.dispatch()` returns it typed. See [Command & Query Result Types](command-query-result-types.md) for the full type system.

## Next Steps

- **[Define a Command →](../../how-to/cqrs/define-command.md)** — step-by-step guide
- **[Command Handlers →](handlers.md)** — the handler protocol
- **[Command Bus →](command-bus.md)** — routing and dispatch
- **[Queries →](queries.md)** — the read-side counterpart
