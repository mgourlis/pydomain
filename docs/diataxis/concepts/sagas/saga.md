# Saga

> **Adoption Level:** 5 — Sagas & Process Managers
> **Module:** `pydomain.cqrs.saga`
> **Prerequisites:** [Domain Events](../ddd/domain-events.md), [Commands](../cqrs/commands.md), [Command Bus](../cqrs/command-bus.md), [Integration Events](../cqrs/integration-events.md)

## What is a Saga?

A **Saga** (or Process Manager) coordinates a long-running business transaction that spans multiple aggregates and services. Unlike a database transaction, a saga cannot rely on a single ACID commit — it sequences local transactions and compensates when something fails.

pydomain models sagas as explicit state machines: each saga instance tracks its current step, listens for domain events that advance it, and dispatches commands to the command bus. Compensation is built-in via a LIFO stack.

## Core concepts

- **Explicit state machine:** A saga always knows its current step and status
- **Event-driven choreography:** Sagas react to domain events, not direct calls
- **Correlation by ID:** A `correlation_id` links all events in the same business process
- **Compensation:** Each forward step can register a compensating command; on failure they execute in LIFO order
- **Human-in-the-loop:** Sagas can suspend, awaiting an external action, with optional timeouts

## The `Saga` base class

```python
from pydomain.cqrs.saga import Saga
from pydomain.cqrs.saga.state import SagaState


class OrderFulfillmentSaga(Saga[SagaState]):
    listens_to = [OrderCreated, PaymentConfirmed, ItemsShipped]
    default_timeout = timedelta(hours=24)
```

Every saga subclass must declare `listens_to` — the list of `DomainEvent` types it handles. The `SagaManager` uses this to route events.

### Two styles for handling events

pydomain supports **declarative** (command-mapper) and **imperative** (handler) styles, both via the `on()` method. See [Declarative vs Imperative](declarative-vs-imperative.md) for guidance on when to use each.

**Declarative (command-mapper):**

```python
self.on(OrderCreated,
        send=lambda e: ReserveItems(order_id=e.order_id),
        step="reserving",
        compensate=lambda e: CancelReservation(order_id=e.order_id))
```

**Imperative (handler):**

```python
self.on(OrderCreated, handler=self.handle_order_created)

async def handle_order_created(self, event: OrderCreated) -> None:
    if event.priority == "high":
        self.dispatch(ReserveItems(order_id=event.order_id, priority=True))
    else:
        self.dispatch(ReserveItems(order_id=event.order_id, priority=False))
```

### Idempotency

The base `handle()` method checks `state.is_event_processed(event.event_id)` before dispatching. Duplicate deliveries are silently ignored. When an event is processed, its ID is recorded via `state.mark_event_processed()`.

## Anatomy of `on()`

The `on()` method accepts these parameters:

| Parameter | Purpose |
|-----------|---------|
| `send` | Command factory — receives event, returns a `Command` |
| `handler` | Custom callable for complex logic (mutually exclusive with `send`) |
| `step` | Sets `current_step` on the saga state |
| `compensate` | Compensation command factory |
| `compensate_description` | Human-readable label for the compensation record |
| `complete` | Mark saga as COMPLETED after this event |
| `suspend` | Suspend saga after this event (human-in-the-loop) |
| `suspend_reason` | Reason for suspension (for audit) |
| `suspend_timeout` | Optional auto-expiry timeout |
| `fail` | Fail saga after dispatching (triggers compensation) |
| `fail_reason` | Reason for failure |
| `resumes_from` | Restrict which step(s) this event can resume from |
| `should_resume` | Inline predicate for step-specific resume logic |

Mutually exclusive combinations are validated at registration time: you cannot set both `complete` and `suspend`, or both `fail` and `complete`, etc.

## Related ADRs

- **ADR-046**: Saga suspend/resume with optional timeouts
- **ADR-049**: Step-based resume authorization (`resumes_from`)
- **ADR-050**: Inline resume predicates (`should_resume`)
- **ADR-052**: Pruning policy for saga history

## Next steps

- [Declarative vs Imperative](declarative-vs-imperative.md) — choose the right style
- [Saga Lifecycle](saga-lifecycle.md) — understand the state machine
- [How to Define a Saga](../../how-to/sagas/define-saga.md) — build your first saga
