# How to Suspend, Resume & Timeout a Saga

> **Adoption Level:** 5 · Prerequisites: [Saga Lifecycle concept](../../concepts/sagas/saga-lifecycle.md), [Define a Saga](define-saga.md)

This guide covers human-in-the-loop patterns — suspending a saga until an external action completes, resuming it when the action arrives, and handling timeouts.

## 1. Suspend a saga inline (declarative)

Use `suspend=True` in `on()` for straightforward wait points:

```python
self.on(PaymentConfirmed,
        send=lambda e: RequestApproval(order_id=e.order_id),
        step="awaiting_approval",
        suspend=True,
        suspend_reason="Large order requires manager approval",
        suspend_timeout=timedelta(hours=48))
```

### Timeout resolution

| Value | Behavior |
|-------|----------|
| `timedelta(hours=48)` | Auto-expires after 48 hours |
| `None` (explicit) | Infinite suspension — never times out |
| Omitted | Uses `default_timeout` from the saga class |
| `USE_DEFAULT_TIMEOUT` sentinel | Same as omitting |

The `USE_DEFAULT_TIMEOUT` sentinel is available when you need to be explicit about falling back to the default:

```python
from pydomain.cqrs.saga.saga import USE_DEFAULT_TIMEOUT

self.on(SomeEvent, ..., suspend=True, suspend_timeout=USE_DEFAULT_TIMEOUT)
```

## 2. Suspend a saga programmatically (imperative)

Call `suspend()` inside a handler:

```python
async def handle_payment(self, event: PaymentConfirmed) -> None:
    if event.amount > 1000:
        self.dispatch(RequestApproval(order_id=event.order_id))
        self.suspend(
            reason=f"High-value order {event.order_id} requires approval",
            timeout=timedelta(hours=48),
        )
    else:
        self.dispatch(ShipItems(order_id=event.order_id))
```

## 3. Control resume with `resumes_from`

Restrict which event types can resume from specific steps:

```python
class OrderFulfillmentSaga(Saga[SagaState]):
    listens_to = [OrderCreated, ManagerApproved, ManagerRejected, TicketResolved]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)

        # ... forward steps ...

        # Only resume from "awaiting_approval" step
        self.on(ManagerApproved,
                send=lambda e: ShipItems(order_id=e.order_id),
                step="shipping",
                resumes_from="awaiting_approval")

        self.on(ManagerRejected,
                send=lambda e: CancelOrder(order_id=e.order_id),
                fail=True,
                fail_reason="Manager rejected the order",
                resumes_from="awaiting_approval")

        # Can resume from multiple steps
        self.on(TicketResolved,
                send=lambda e: ResumeProcessing(order_id=e.order_id),
                step="resuming",
                resumes_from=["awaiting_approval", "awaiting_support"])
```

If `resumes_from` is `None` (default), the event can resume the saga from any step.

## 4. Add inline predicates with `should_resume`

For conditional resume logic beyond step names:

```python
self.on(PaymentConfirmed,
        handler=self.handle_payment_confirmed,
        should_resume=lambda e: e.amount > Money(amount=100, currency="EUR"))
```

The predicate receives the event and returns `True` to allow resume, `False` to keep the saga suspended. Predicates are evaluated **after** `resumes_from` — both must pass.

## 5. Override `should_resume` for complex logic

For multi-condition resume logic, override the method:

```python
def should_resume(self, event: DomainEvent) -> bool:
    # Delegate to base logic for resumes_from + predicates
    if not super().should_resume(event):
        return False

    # Custom check: only resume during business hours
    now = datetime.now(UTC)
    if now.hour < 9 or now.hour > 17:
        return False

    return True
```

## 6. Handle timeouts

Schedule `process_timeouts()` to run periodically:

```python
# Run every 60 seconds
await manager.process_timeouts(limit=50)
```

Override `on_timeout()` for custom timeout behavior:

```python
class OrderFulfillmentSaga(Saga[SagaState]):
    async def on_timeout(self) -> None:
        if self.state.current_step == "awaiting_approval":
            # Escalate to senior manager instead of failing
            self.dispatch(EscalateToSeniorManager(
                order_id=self.state.metadata["order_id"]
            ))
            self.suspend(
                reason="Escalated to senior manager",
                timeout=timedelta(hours=24),
            )
        else:
            # Default: fail with compensation
            await super().on_timeout()
```

If `on_timeout()` doesn't resolve the suspension (saga is still SUSPENDED), the manager force-fails it.

## 7. Test suspend/resume

```python
@pytest.mark.anyio
async def test_suspend_resume_flow():
    repo = FakeSagaRepository()
    cmd_bus = FakeCommandBus()
    registry = SagaRegistry()
    registry.register_saga(OrderFulfillmentSaga)
    manager = SagaManager(repo, registry, cmd_bus)

    correlation_id = uuid4()
    order_id = uuid4()

    # Step 1: OrderCreated starts the saga
    await manager.handle(OrderCreated(
        event_id=uuid4(), order_id=order_id, customer_id=uuid4(),
        correlation_id=correlation_id,
    ))

    # Step 2: PaymentConfirmed triggers suspension
    await manager.handle(PaymentConfirmed(
        event_id=uuid4(), order_id=order_id, amount=2000,
        correlation_id=correlation_id,
    ))

    state = await repo.find_by_correlation_id(correlation_id, "OrderFulfillmentSaga")
    assert state.status == SagaStatus.SUSPENDED
    assert state.suspension_reason is not None

    # Step 3: ManagerApproved resumes the saga
    await manager.handle(ManagerApproved(
        event_id=uuid4(), order_id=order_id,
        correlation_id=correlation_id,
    ))

    state = await repo.find_by_correlation_id(correlation_id, "OrderFulfillmentSaga")
    assert state.status == SagaStatus.RUNNING
    assert state.suspended_at is None
```

## Expected outcome

A saga that suspends for human action, resumes when the expected event arrives, respects step-based authorization, and auto-expires on timeout.

## Next steps

- [Handle Saga Errors](saga-error-handling.md) — retry, recovery, and failure modes
- [Configure a Saga Manager](configure-saga-manager.md) — full wiring including recovery loops
- [Prune Saga History](saga-pruning.md) — cap unbounded growth
