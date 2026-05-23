# Recipe: OTP Suspend/Resume Saga

> **Adoption Level:** 5 · Prerequisites: [Suspend, Resume & Timeout how-to](../sagas/saga-suspend-resume-timeout.md), [Saga Orchestration recipe](saga-orchestration.md), [Saga Lifecycle concept](../../concepts/sagas/saga-lifecycle.md)

This recipe builds a human-in-the-loop saga that suspends for an OTP confirmation before completing a high-value transaction. It demonstrates suspend, resume with `resumes_from`, custom `on_timeout()` escalation, and timeout expiry.

## What You'll Build

A **Transaction Approval Saga** with:

- **Events:** `TransactionInitiated`, `OTPSubmitted`, `OTPExpired`
- **Commands:** `DebitAccount`, `CreditAccount`, `ReverseDebit` (compensation) + `SendOTP`, `EscalateToFraudTeam`
- **Saga:** `TransactionApprovalSaga` — declarative style with suspend + custom timeout handler
- **Suspend/Resume:** Suspends on OTP request, resumes only from the `"awaiting_otp"` step
- **Timeout:** Escalates to fraud team instead of failing immediately

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

```python
# domain/transaction_approval_saga.py
from datetime import timedelta
from pydomain.cqrs.saga import Saga
from pydomain.cqrs.saga.state import SagaState

from domain.events import TransactionInitiated, OTPSubmitted, OTPExpired
from application.commands import (
    SendOTP, DebitAccount, CreditAccount,
    ReverseDebit, EscalateToFraudTeam,
)


class TransactionApprovalSaga(Saga[SagaState]):
    listens_to = [TransactionInitiated, OTPSubmitted, OTPExpired]
    default_timeout = timedelta(hours=24)

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)

        # Step 1: Debit the source account, then send OTP to the user
        self.on(TransactionInitiated,
                send=lambda e: DebitAccount(
                    transaction_id=e.transaction_id,
                    from_account=e.from_account,
                    amount=e.amount,
                ),
                step="debiting",
                compensate=lambda e: ReverseDebit(
                    transaction_id=e.transaction_id,
                    from_account=e.from_account,
                    amount=e.amount,
                ),
                compensate_description="Reverse debit")

        # Step 2: After debit, send OTP and SUSPEND
        self.on(TransactionInitiated,
                send=lambda e: SendOTP(transaction_id=e.transaction_id),
                step="awaiting_otp",
                suspend=True,
                suspend_reason="Waiting for OTP confirmation",
                suspend_timeout=timedelta(minutes=10),
                compensate=lambda e: ReverseDebit(
                    transaction_id=e.transaction_id,
                    from_account=e.from_account,
                    amount=e.amount,
                ),
                compensate_description="Reverse debit on OTP timeout")

        # Step 3: OTP submitted → credit the destination
        self.on(OTPSubmitted,
                send=lambda e: CreditAccount(
                    transaction_id=e.transaction_id,
                    to_account=self.state.metadata.get("to_account", ""),
                    amount=self.state.metadata.get("amount", 0),
                ),
                step="crediting",
                resumes_from="awaiting_otp",
                complete=True)

        # Step 4: OTP expired (external event) → escalate
        self.on(OTPExpired,
                send=lambda e: EscalateToFraudTeam(
                    transaction_id=e.transaction_id,
                    reason="User failed to submit OTP",
                ),
                fail=True,
                fail_reason="OTP expired — transaction cancelled",
                resumes_from="awaiting_otp")

    async def on_timeout(self) -> None:
        """Custom timeout: escalate instead of immediate failure."""
        if self.state.current_step == "awaiting_otp":
            transaction_id = self.state.metadata.get("transaction_id")
            self.dispatch(EscalateToFraudTeam(
                transaction_id=transaction_id,
                reason=f"Saga {self.state.id} timed out awaiting OTP",
            ))
            # Stay suspended for fraud team review
            self.suspend(
                reason="Escalated to fraud team after OTP timeout",
                timeout=timedelta(hours=48),
            )
        else:
            await super().on_timeout()
```

**Note:** The saga registers the same event (`TransactionInitiated`) twice with different `step` values. The `SagaManager` calls `handle()` once per event; the `_handle_event()` method dispatches to the first registered handler. To chain two steps from one event, use an imperative handler instead.

For a **two-step single-event saga**, use an imperative handler:

```python
class TransactionApprovalSaga(Saga[SagaState]):
    listens_to = [TransactionInitiated, OTPSubmitted, OTPExpired]
    default_timeout = timedelta(hours=24)

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)

        self.on(TransactionInitiated, handler=self.handle_transaction_initiated)

        self.on(OTPSubmitted,
                send=lambda e: CreditAccount(
                    transaction_id=e.transaction_id,
                    to_account=self.state.metadata["to_account"],
                    amount=self.state.metadata["amount"],
                ),
                step="crediting",
                resumes_from="awaiting_otp",
                complete=True)

        self.on(OTPExpired,
                send=lambda e: EscalateToFraudTeam(
                    transaction_id=e.transaction_id,
                    reason="OTP expired",
                ),
                fail=True,
                fail_reason="OTP expired",
                resumes_from="awaiting_otp")

    async def handle_transaction_initiated(self, event: TransactionInitiated) -> None:
        # Store context for later steps
        self.state.metadata["transaction_id"] = event.transaction_id
        self.state.metadata["to_account"] = event.to_account
        self.state.metadata["amount"] = event.amount

        # Step 1: Debit
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

        # Step 2: Send OTP and suspend
        self.state.current_step = "awaiting_otp"
        self.dispatch(SendOTP(transaction_id=event.transaction_id))
        self.suspend(
            reason="Waiting for OTP confirmation",
            timeout=timedelta(minutes=10),
        )

        # Push a second compensation for this step
        self.add_compensation(ReverseDebit(
            transaction_id=event.transaction_id,
            from_account=event.from_account,
            amount=event.amount,
        ), description="Reverse debit on OTP timeout")
```

## Step 4: Wiring

```python
# infrastructure/wiring.py
from pydomain.cqrs.saga import SagaRegistry, SagaManager

registry = SagaRegistry()
registry.register_saga(TransactionApprovalSaga)

manager = SagaManager(
    repository=saga_repository,
    registry=registry,
    command_bus=command_bus,
)
manager.bind_to(message_bus)
```

## Step 5: Test Happy Path (Initiate → OTP → Complete)

```python
# tests/test_transaction_approval_saga.py
import pytest
from uuid import uuid4
from pydomain.cqrs.saga import SagaRegistry, SagaManager, SagaStatus
from pydomain.testing.fake_saga_repository import FakeSagaRepository


class FakeCommandBus:
    def __init__(self):
        self.dispatched: list = []

    async def dispatch(self, command):
        self.dispatched.append(command)


@pytest.mark.anyio
async def test_initiate_suspend_resume_complete():
    repo = FakeSagaRepository()
    cmd_bus = FakeCommandBus()
    registry = SagaRegistry()
    registry.register_saga(TransactionApprovalSaga)

    manager = SagaManager(
        repository=repo,
        registry=registry,
        command_bus=cmd_bus,
    )

    correlation_id = uuid4()
    tx_id = uuid4()

    # Step 1: Initiate transaction → saga starts, debits, sends OTP, suspends
    await manager.handle(TransactionInitiated(
        event_id=uuid4(),
        transaction_id=tx_id,
        from_account="ACC-001",
        to_account="ACC-002",
        amount=50000,
        correlation_id=correlation_id,
    ))

    state = await repo.find_by_correlation_id(
        correlation_id, "TransactionApprovalSaga"
    )
    assert state.status == SagaStatus.SUSPENDED
    assert state.current_step == "awaiting_otp"
    assert state.suspension_reason == "Waiting for OTP confirmation"
    assert state.timeout_at is not None  # 10-minute timeout set

    # Forward commands dispatched: DebitAccount + SendOTP
    cmd_types = [type(c).__name__ for c in cmd_bus.dispatched]
    assert "DebitAccount" in cmd_types
    assert "SendOTP" in cmd_types

    # Step 2: OTP submitted → saga resumes, credits, completes
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

    # CreditAccount was dispatched
    credit_cmds = [c for c in cmd_bus.dispatched if isinstance(c, CreditAccount)]
    assert len(credit_cmds) == 1
    assert credit_cmds[0].to_account == "ACC-002"
    assert credit_cmds[0].amount == 50000
```

## Step 6: Test OTP Expired (Suspend → Expire → Fail)

```python
@pytest.mark.anyio
async def test_otp_expired_fails_with_compensation():
    repo = FakeSagaRepository()
    cmd_bus = FakeCommandBus()
    registry = SagaRegistry()
    registry.register_saga(TransactionApprovalSaga)

    manager = SagaManager(
        repository=repo,
        registry=registry,
        command_bus=cmd_bus,
    )

    correlation_id = uuid4()
    tx_id = uuid4()

    # Initiate → saga suspends awaiting OTP
    await manager.handle(TransactionInitiated(
        event_id=uuid4(),
        transaction_id=tx_id,
        from_account="ACC-001",
        to_account="ACC-002",
        amount=50000,
        correlation_id=correlation_id,
    ))

    state = await repo.find_by_correlation_id(
        correlation_id, "TransactionApprovalSaga"
    )
    assert state.status == SagaStatus.SUSPENDED

    # OTP expired event → saga fails with compensation
    await manager.handle(OTPExpired(
        event_id=uuid4(),
        transaction_id=tx_id,
        correlation_id=correlation_id,
    ))

    state = await repo.get_by_id(state.id)
    assert state.status == SagaStatus.COMPENSATED
    assert state.error == "OTP expired — transaction cancelled"

    # Compensation commands were dispatched (ReverseDebit × 2, LIFO)
    # plus EscalateToFraudTeam was dispatched as forward command before fail
    cmd_type_names = [type(c).__name__ for c in cmd_bus.dispatched]
    assert "EscalateToFraudTeam" in cmd_type_names

    # Compensation stack is empty
    assert len(state.compensation_stack) == 0
```

## Step 7: Test Timeout Expiry

```python
from datetime import UTC, datetime, timedelta
from pydomain.cqrs.saga import SagaManager


@pytest.mark.anyio
async def test_timeout_escalates_instead_of_failing():
    repo = FakeSagaRepository()
    cmd_bus = FakeCommandBus()
    registry = SagaRegistry()
    registry.register_saga(TransactionApprovalSaga)

    manager = SagaManager(
        repository=repo,
        registry=registry,
        command_bus=cmd_bus,
    )

    correlation_id = uuid4()
    tx_id = uuid4()

    # Initiate → saga suspends
    await manager.handle(TransactionInitiated(
        event_id=uuid4(),
        transaction_id=tx_id,
        from_account="ACC-001",
        to_account="ACC-002",
        amount=50000,
        correlation_id=correlation_id,
    ))

    # Manually expire the timeout
    state = await repo.find_by_correlation_id(
        correlation_id, "TransactionApprovalSaga"
    )
    state.timeout_at = datetime.now(UTC) - timedelta(minutes=1)  # Past
    await repo.save(state)

    # Process timeouts
    await manager.process_timeouts(limit=10)

    state = await repo.get_by_id(state.id)
    # Custom on_timeout() re-suspends with escalation, doesn't fail
    assert state.status == SagaStatus.SUSPENDED
    assert "Escalated to fraud team" in state.suspension_reason
    assert state.timeout_at is not None

    # EscalateToFraudTeam was dispatched
    escalation_cmds = [
        c for c in cmd_bus.dispatched
        if isinstance(c, EscalateToFraudTeam)
    ]
    assert len(escalation_cmds) >= 1
```

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
          │     ├── EscalateToFraudTeam (forward)
          │     ├── fail(reason, compensate=True)
          │     ├── execute_compensations() → ReverseDebit (LIFO)
          │     └── COMPENSATED
          │
          └── timeout expires (10 min)
                ├── on_timeout()
                ├── EscalateToFraudTeam (forward)
                └── suspend("Escalated to fraud team", timeout=48h)
```

## What's Next?

- [Saga Orchestration recipe](saga-orchestration.md) — full end-to-end saga flow
- [Handle Saga Errors how-to](../sagas/saga-error-handling.md) — retry and recovery
- [All Modules Integration recipe](all-modules.md) — saga with full DDD + CQRS + ES
