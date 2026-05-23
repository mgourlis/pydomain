# How to Implement Saga Compensation

> **Adoption Level:** 5 · Prerequisites: [Saga Compensation concept](../../concepts/sagas/saga-compensation.md), [Define a Saga](define-saga.md)

This guide shows how to register compensating commands, trigger compensation on failure, and handle compensation edge cases.

## 1. Register compensation in declarative style

Add `compensate=` to each `on()` call for steps that need undoing:

```python
class OrderFulfillmentSaga(Saga[SagaState]):
    listens_to = [OrderCreated, PaymentConfirmed, ItemsShipped]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)

        self.on(OrderCreated,
                send=lambda e: ReserveItems(order_id=e.order_id),
                step="reserving",
                compensate=lambda e: CancelReservation(order_id=e.order_id),
                compensate_description="Cancel item reservation")

        self.on(PaymentConfirmed,
                send=lambda e: ChargeCustomer(order_id=e.order_id, amount=e.amount),
                step="charging",
                compensate=lambda e: RefundCustomer(order_id=e.order_id, amount=e.amount),
                compensate_description="Refund charged amount")

        self.on(ItemsShipped,
                send=lambda e: NotifyCustomer(order_id=e.order_id),
                step="completed",
                complete=True)
```

## 2. Register compensation in imperative style

Call `add_compensation()` inside handler methods:

```python
class OrderFulfillmentSaga(Saga[SagaState]):
    listens_to = [OrderCreated]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(OrderCreated, handler=self.handle_order_created)

    async def handle_order_created(self, event: OrderCreated) -> None:
        self.state.current_step = "reserving"
        self.dispatch(ReserveItems(order_id=event.order_id))
        self.add_compensation(
            CancelReservation(order_id=event.order_id),
            description=f"Cancel reservation for order {event.order_id}"
        )
```

The description is stored in the `CompensationRecord` for audit — include enough context to understand the undo operation.

## 3. Trigger compensation with `fail()`

Compensation runs automatically when `fail(reason, compensate=True)` is called:

```python
async def handle_payment(self, event: PaymentConfirmed) -> None:
    try:
        result = await self.payment_gateway.charge(event.amount)
    except PaymentGatewayError:
        await self.fail("Payment gateway unreachable", compensate=True)
        return
    self.dispatch(ShipItems(order_id=event.order_id))
```

Or inline with the `fail=True` flag:

```python
self.on(PaymentFailed,
        send=lambda e: MarkOrderFailed(order_id=e.order_id),
        fail=True,
        fail_reason="Payment was declined by the gateway")
```

## 4. Handle compensation with `fail_reason` callable

The `fail_reason` parameter accepts both static strings and callables:

```python
self.on(PaymentFailed,
        send=lambda e: MarkOrderFailed(order_id=e.order_id),
        fail=True,
        fail_reason=lambda e: f"Payment {e.payment_id} declined: {e.decline_reason}")
```

The callable receives the event and returns the reason string. Similarly, `compensate_description` accepts callables:

```python
self.on(OrderCreated,
        send=lambda e: ReserveItems(order_id=e.order_id),
        compensate=lambda e: CancelReservation(order_id=e.order_id),
        compensate_description=lambda e: f"Cancel reservation for order {e.order_id}")
```

## 5. Execute compensation programmatically

For fine-grained control, call `execute_compensations()` directly:

```python
async def handle_critical_failure(self, event: CriticalFailure) -> None:
    # Discard any forward commands queued so far
    await self.execute_compensations()
    # The SagaManager will dispatch the compensation commands
```

The method:
1. Sets `status = COMPENSATING`
2. Clears any queued forward commands
3. Pops and hydrates each `CompensationRecord` (LIFO)
4. Queues them via `self.dispatch()`
5. Records hydration failures in `state.failed_compensations`

## 6. Verify compensation in tests

```python
@pytest.mark.anyio
async def test_compensation_on_failure():
    repo = FakeSagaRepository()
    registry = SagaRegistry()
    registry.register_saga(OrderFulfillmentSaga)
    cmd_bus = FakeCommandBus()

    manager = SagaManager(repo, registry, cmd_bus)
    correlation_id = uuid4()

    # Step 1: OrderCreated → should register a compensation
    await manager.handle(OrderCreated(
        event_id=uuid4(), order_id=uuid4(), customer_id=uuid4(),
        correlation_id=correlation_id,
    ))

    state = await repo.find_by_correlation_id(correlation_id, "OrderFulfillmentSaga")
    assert len(state.compensation_stack) == 1
    assert state.compensation_stack[0].command_type == "CancelReservation"

    # Step 2: Trigger failure
    await manager.handle(PaymentFailed(
        event_id=uuid4(), order_id=uuid4(),
        correlation_id=correlation_id,
    ))

    state = await repo.find_by_correlation_id(correlation_id, "OrderFulfillmentSaga")
    # Compensation stack should be drained (LIFO executed)
    assert len(state.compensation_stack) == 0
    assert state.status in (SagaStatus.COMPENSATED, SagaStatus.FAILED)
```

## Next steps

- [Handle Saga Errors](saga-error-handling.md) — retry, recovery, and failure modes
- [Suspend, Resume & Timeout](saga-suspend-resume-timeout.md) — add human-in-the-loop steps
- [Saga Compensation concept](../../concepts/sagas/saga-compensation.md) — architecture deep dive
