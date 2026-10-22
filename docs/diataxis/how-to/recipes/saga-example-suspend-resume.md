# Recipe: OTP Suspend/Resume Saga

<!-- docs-test: runnable -->

> **Adoption Level:** 5 · Prerequisites: [Suspend, Resume & Timeout how-to](../sagas/saga-suspend-resume-timeout.md), [Saga Orchestration recipe](saga-orchestration.md), [Saga Lifecycle concept](../../concepts/sagas/saga-lifecycle.md)

This recipe builds a human-in-the-loop saga that suspends for an OTP confirmation before completing a high-value transaction. It demonstrates suspend, resume with `resumes_from`, an `on_timeout()` that escalates and then compensates, and timeout expiry.

## What You'll Build

A **Transaction Approval Saga** with:

- **Events:** `TransactionInitiated`, `OTPSubmitted`, `OTPExpired`
- **Commands:** `DebitAccount`, `CreditAccount`, `ReverseDebit` (compensation) + `SendOTP`, `EscalateToFraudTeam`
- **Saga:** `TransactionApprovalSaga`: an imperative handler for the first event (several steps from one event), declarative handlers for the others, and a custom timeout handler
- **Suspend/Resume:** suspends on the OTP request and resumes only from the `"awaiting_otp"` step
- **Timeout:** escalates to the fraud team, then fails the saga and reverses the debit

## Step 1: Events

```python
# domain/events.py
from uuid import UUID
from pydomain.ddd.domain_event import DomainEvent


class TransactionInitiated(DomainEvent):
    transaction_id: UUID
    from_account: str
    to_account: str
    amount: int
    correlation_id: UUID


class OTPSubmitted(DomainEvent):
    transaction_id: UUID
    otp_code: str
    correlation_id: UUID


class OTPExpired(DomainEvent):
    transaction_id: UUID
    correlation_id: UUID
```

## Step 2: Commands

```python
# application/commands.py
from uuid import UUID
from pydomain.cqrs.commands import Command


class SendOTP(Command[UUID]):
    transaction_id: UUID


class DebitAccount(Command[UUID]):
    transaction_id: UUID
    from_account: str
    amount: int


class CreditAccount(Command[UUID]):
    transaction_id: UUID
    to_account: str
    amount: int


class ReverseDebit(Command[UUID]):
    transaction_id: UUID
    from_account: str
    amount: int


class EscalateToFraudTeam(Command[UUID]):
    transaction_id: UUID
    reason: str
```

## Step 3: The Saga

`Saga.on()` keeps one handler per event type: a second `on()` for the same event replaces the first. To run several steps for one event, use one imperative handler, as below.

```python
# domain/transaction_approval_saga.py
from datetime import timedelta
from pydomain.cqrs.saga import Saga
from pydomain.cqrs.saga.state import SagaState


class TransactionApprovalSaga(Saga[SagaState]):
    listens_to = [TransactionInitiated, OTPSubmitted, OTPExpired]
    default_timeout = timedelta(hours=24)

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)

        self.on(TransactionInitiated, handler=self.handle_transaction_initiated)

        # OTP submitted → credit the destination and complete.
        self.on(OTPSubmitted,
                send=lambda e: CreditAccount(
                    transaction_id=e.transaction_id,
                    to_account=self.state.metadata["to_account"],
                    amount=self.state.metadata["amount"],
                ),
                step="crediting",
                resumes_from="awaiting_otp",
                complete=True)

        # OTP expired → escalate, then fail. The forward command goes out
        # first, then the compensations.
        self.on(OTPExpired,
                send=lambda e: EscalateToFraudTeam(
                    transaction_id=e.transaction_id,
                    reason="OTP expired",
                ),
                fail=True,
                fail_reason="OTP expired",
                resumes_from="awaiting_otp")

    async def handle_transaction_initiated(self, event: TransactionInitiated) -> None:
        # Keep what later steps need
        self.state.metadata["transaction_id"] = event.transaction_id
        self.state.metadata["to_account"] = event.to_account
        self.state.metadata["amount"] = event.amount

        # Step 1: debit, and register how to undo it
        self.state.current_step = "debiting"
        self.dispatch(DebitAccount(
            transaction_id=event.transaction_id,
            from_account=event.from_account,
            amount=event.amount,
        ))
        self.add_compensation(ReverseDebit(
            transaction_id=event.transaction_id,
            from_account=event.from_account,
            amount=event.amount,
        ), description="Reverse debit")

        # Step 2: send the OTP and suspend until it is submitted
        self.state.current_step = "awaiting_otp"
        self.dispatch(SendOTP(transaction_id=event.transaction_id))
        self.suspend(
            reason="Waiting for OTP confirmation",
            timeout=timedelta(minutes=10),
        )

    async def on_timeout(self) -> None:
        """Escalate, then fail: the suspension must be resolved here."""
        if self.state.current_step == "awaiting_otp":
            self.dispatch(EscalateToFraudTeam(
                transaction_id=self.state.metadata["transaction_id"],
                reason=f"Saga {self.state.id} timed out awaiting OTP",
            ))
            await self.fail("OTP timed out; escalated to the fraud team")
        else:
            await super().on_timeout()
```

If `on_timeout()` leaves the saga suspended, `SagaManager.process_timeouts()` force-fails it, without compensating. So a timeout handler that escalates must end the suspension itself: here it fails the saga, which sends the escalation first and then the compensation.

## Step 4: Wiring

<!-- docs-test: skip -->
```python
# infrastructure/wiring.py
from pydomain.cqrs.saga import SagaRegistry, SagaManager

registry = SagaRegistry()
registry.register_saga(TransactionApprovalSaga)

manager = SagaManager(
    repository=saga_repository,
    registry=registry,
    command_bus=message_bus,  # a MessageBus: it runs the commands and publishes their events
)
manager.bind_to(message_bus)
```

## Step 5: Test Happy Path (Initiate → OTP → Complete)

The tests use a recording dispatcher instead of a bus. Production wiring passes the `MessageBus`.

```python
# tests/test_transaction_approval_saga.py
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from pydomain.cqrs.saga import SagaManager, SagaRegistry, SagaStatus
from pydomain.testing.fake_saga_repository import FakeSagaRepository

pytestmark = pytest.mark.anyio  # the async tests run on the anyio pytest plugin


class RecordingDispatcher:
    """Records commands. It is a CommandDispatcher; production wiring passes the MessageBus."""

    def __init__(self):
        self.dispatched: list = []

    async def dispatch(self, command):
        self.dispatched.append(command)


def build_manager():
    repo = FakeSagaRepository()
    dispatcher = RecordingDispatcher()
    registry = SagaRegistry()
    registry.register_saga(TransactionApprovalSaga)
    manager = SagaManager(repository=repo, registry=registry, command_bus=dispatcher)
    return repo, dispatcher, manager


def transaction_initiated(transaction_id, correlation_id):
    return TransactionInitiated(
        event_id=uuid4(),
        transaction_id=transaction_id,
        from_account="ACC-001",
        to_account="ACC-002",
        amount=50000,
        correlation_id=correlation_id,
    )


async def test_initiate_suspend_resume_complete():
    repo, dispatcher, manager = build_manager()
    correlation_id = uuid4()
    tx_id = uuid4()

    # Initiate: the saga debits, sends the OTP and suspends
    await manager.handle(transaction_initiated(tx_id, correlation_id))

    state = await repo.find_by_correlation_id(
        correlation_id, "TransactionApprovalSaga"
    )
    assert state.status == SagaStatus.SUSPENDED
    assert state.current_step == "awaiting_otp"
    assert state.suspension_reason == "Waiting for OTP confirmation"
    assert state.timeout_at is not None  # 10-minute timeout set
    assert [type(c).__name__ for c in dispatcher.dispatched] == [
        "DebitAccount",
        "SendOTP",
    ]

    # OTP submitted: the saga resumes, credits and completes
    await manager.handle(OTPSubmitted(
        event_id=uuid4(),
        transaction_id=tx_id,
        otp_code="123456",
        correlation_id=correlation_id,
    ))

    state = await repo.get_by_id(state.id)
    assert state.status == SagaStatus.COMPLETED
    assert state.current_step == "crediting"
    assert state.suspended_at is None

    credit_cmds = [c for c in dispatcher.dispatched if isinstance(c, CreditAccount)]
    assert len(credit_cmds) == 1
    assert credit_cmds[0].to_account == "ACC-002"
    assert credit_cmds[0].amount == 50000
```

## Step 6: Test OTP Expired (Suspend → Expire → Fail)

```python
async def test_otp_expired_fails_with_compensation():
    repo, dispatcher, manager = build_manager()
    correlation_id = uuid4()
    tx_id = uuid4()

    await manager.handle(transaction_initiated(tx_id, correlation_id))

    state = await repo.find_by_correlation_id(
        correlation_id, "TransactionApprovalSaga"
    )
    assert state.status == SagaStatus.SUSPENDED

    # OTP expired: the saga fails and compensates
    await manager.handle(OTPExpired(
        event_id=uuid4(),
        transaction_id=tx_id,
        correlation_id=correlation_id,
    ))

    state = await repo.get_by_id(state.id)
    assert state.status == SagaStatus.COMPENSATED
    assert state.error == "OTP expired"
    assert len(state.compensation_stack) == 0

    # The escalation (a forward command) goes out before the compensation
    assert [type(c).__name__ for c in dispatcher.dispatched] == [
        "DebitAccount",
        "SendOTP",
        "EscalateToFraudTeam",
        "ReverseDebit",
    ]
```

## Step 7: Test Timeout Expiry

```python
async def test_timeout_escalates_then_compensates():
    repo, dispatcher, manager = build_manager()
    correlation_id = uuid4()
    tx_id = uuid4()

    await manager.handle(transaction_initiated(tx_id, correlation_id))

    # Expire the timeout by hand
    state = await repo.find_by_correlation_id(
        correlation_id, "TransactionApprovalSaga"
    )
    state.timeout_at = datetime.now(UTC) - timedelta(minutes=1)
    await repo.save(state)

    await manager.process_timeouts(limit=10)

    state = await repo.get_by_id(state.id)
    # on_timeout() escalated and failed the saga, so the debit is reversed
    assert state.status == SagaStatus.COMPENSATED
    assert state.error == "OTP timed out; escalated to the fraud team"
    assert [type(c).__name__ for c in dispatcher.dispatched][2:] == [
        "EscalateToFraudTeam",
        "ReverseDebit",
    ]
```

<!-- docs-test: check
await test_initiate_suspend_resume_complete()
await test_otp_expired_fails_with_compensation()
await test_timeout_escalates_then_compensates()
-->

## Architecture Recap

```
TransactionInitiated
  │
  ├── DebitAccount (forward)
  ├── add_compensation(ReverseDebit)
  ├── SendOTP (forward)
  ├── suspend("Waiting for OTP", timeout=10min)
  │
  └── [saga SUSPENDED]
          │
          ├── OTPSubmitted received
          │     ├── resume()
          │     ├── CreditAccount (forward)
          │     └── complete()
          │
          ├── OTPExpired received
          │     ├── EscalateToFraudTeam (forward, sent first)
          │     ├── fail(reason, compensate=True)
          │     ├── ReverseDebit (compensation)
          │     └── COMPENSATED
          │
          └── timeout expires (10 min)
                ├── on_timeout()
                ├── EscalateToFraudTeam (forward, sent first)
                ├── fail(reason, compensate=True)
                ├── ReverseDebit (compensation)
                └── COMPENSATED
```

## What's Next?

- [Saga Orchestration recipe](saga-orchestration.md) — full end-to-end saga flow
- [Handle Saga Errors how-to](../sagas/saga-error-handling.md) — retry and recovery
- [All Modules Integration recipe](all-modules.md) — saga with full DDD + CQRS + ES
