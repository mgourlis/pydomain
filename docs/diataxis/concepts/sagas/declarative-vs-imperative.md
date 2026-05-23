# Declarative vs Imperative Saga Style

> **Adoption Level:** 5 — Sagas & Process Managers
> **Prerequisites:** [Saga](saga.md)

pydomain sagas support two distinct styles for defining event handlers. Both use the `on()` method but serve different needs.

## Quick comparison

| | Declarative (command-mapper) | Imperative (handler) |
|---|---|---|
| **Best for** | Straightforward mappings | Conditional logic, multi-branch dispatch |
| **Complexity** | Low — one lambda/function per event | Medium — full method body |
| **Compensation** | Built-in via `compensate=` parameter | Manual via `self.add_compensation()` |
| **Lifecycle flags** | Declared in `on()` (`complete=`, `suspend=`, `fail=`) | Called explicitly in handler (`self.complete()`, etc.) |
| **Readability** | High — single declaration per transition | Medium — logic spread across methods |

## Declarative style (command-mapper)

Use when an event maps directly to a single command dispatch. The framework builds the handler from the parameters you provide.

```python
class OrderFulfillmentSaga(Saga[SagaState]):
    listens_to = [OrderCreated, PaymentConfirmed, ItemsShipped]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)

        self.on(OrderCreated,
                send=lambda e: ReserveItems(order_id=e.order_id),
                step="reserving",
                compensate=lambda e: CancelReservation(order_id=e.order_id))

        self.on(PaymentConfirmed,
                send=lambda e: ShipItems(order_id=e.order_id),
                step="shipping",
                compensate=lambda e: RefundPayment(order_id=e.order_id))

        self.on(ItemsShipped,
                send=lambda e: NotifyCustomer(order_id=e.order_id),
                step="completed",
                complete=True)
```

**When to use:**
- Each event triggers exactly one command
- No conditional branching needed
- The mapping is stable and unlikely to change
- You want the compensation declaration colocated with the forward step

## Imperative style (handler)

Use when an event requires conditional logic, multiple command dispatches, or interaction with external state before dispatching.

```python
class OrderFulfillmentSaga(Saga[SagaState]):
    listens_to = [OrderCreated, PaymentConfirmed, ItemsShipped]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)

        self.on(OrderCreated, handler=self.handle_order_created)
        self.on(PaymentConfirmed, handler=self.handle_payment)
        self.on(ItemsShipped, handler=self.handle_shipment)

    async def handle_order_created(self, event: OrderCreated) -> None:
        self.state.current_step = "reserving"

        if event.priority == "high":
            self.dispatch(ReserveItems(order_id=event.order_id, priority=True))
            self.add_compensation(
                CancelReservation(order_id=event.order_id),
                description=f"Cancel high-priority reservation for {event.order_id}"
            )
        elif event.priority == "low":
            self.dispatch(ReserveItems(order_id=event.order_id, priority=False))
            self.add_compensation(
                CancelReservation(order_id=event.order_id),
                description=f"Cancel low-priority reservation for {event.order_id}"
            )
        else:
            self.suspend(reason=f"Unknown priority {event.priority} for order {event.order_id}")

    async def handle_payment(self, event: PaymentConfirmed) -> None:
        self.state.current_step = "shipping"

        if event.amount > Money(amount=1000, currency="EUR"):
            # Large order — requires fraud check
            self.dispatch(RequestFraudCheck(order_id=event.order_id))
            self.dispatch(ShipItems(order_id=event.order_id))
        else:
            self.dispatch(ShipItems(order_id=event.order_id))

    async def handle_shipment(self, event: ItemsShipped) -> None:
        self.state.current_step = "completed"
        self.complete()
```

**When to use:**
- Conditional dispatch based on event data
- Multiple commands dispatched from a single event
- Need to inspect or update saga state metadata before dispatching
- External service calls or side effects needed

## Imperative style via `_handle_event` override

For maximum control, override `_handle_event` directly. This is the most imperative option — you own the full dispatch:

```python
class OrderFulfillmentSaga(Saga[SagaState]):
    listens_to = [OrderCreated, PaymentConfirmed, ItemsShipped]

    async def _handle_event(self, event: DomainEvent) -> None:
        match event:
            case OrderCreated() as e:
                self.dispatch(ReserveItems(order_id=e.order_id))
                self.add_compensation(CancelReservation(order_id=e.order_id))
                self.state.current_step = "reserving"

            case PaymentConfirmed() as e if e.amount > Money(amount=1000, currency="EUR"):
                self.dispatch(RequestFraudCheck(order_id=e.order_id))
                self.dispatch(ShipItems(order_id=e.order_id))
                self.state.current_step = "shipping"

            case PaymentConfirmed() as e:
                self.dispatch(ShipItems(order_id=e.order_id))
                self.state.current_step = "shipping"

            case ItemsShipped():
                self.complete()

            case _:
                raise SagaHandlerNotFoundError(
                    f"No handler for {type(event).__name__}"
                )
```

**When to use:**
- You prefer `match`/`case` over individual `on()` registrations
- All handlers live in one place for readability
- You need full control over the dispatch flow

> **Note:** Overriding `_handle_event` bypasses `on()` registrations entirely. Choose one approach per saga — don't mix them.

## Mixing styles

Within a single saga class you can freely mix declarative and handler-style `on()` registrations:

```python
self.on(OrderCreated,
        send=lambda e: ReserveItems(order_id=e.order_id),
        step="reserving")  # Declarative — simple

self.on(PaymentConfirmed,
        handler=self.handle_payment)  # Imperative — needs branching
```

You cannot, however, mix `_handle_event` override with `on()` registrations — the override takes precedence.

## Decision guide

```
Can this event be handled with a single command, no conditions?
├── Yes → Declarative (command-mapper)
└── No  → Does the handler need match/case or full dispatch control?
          ├── Yes → Override _handle_event
          └── No  → Imperative (handler with on())
```

## Next steps

- [Saga Lifecycle](saga-lifecycle.md) — understand the state machine transitions
- [How to Define a Saga](../../how-to/sagas/define-saga.md) — build a saga from scratch
