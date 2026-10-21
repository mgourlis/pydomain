"""Shared synthetic types for saga probes."""
from __future__ import annotations
import logging
from typing import Any
from uuid import uuid4, UUID

from pydomain.cqrs.commands import Command, EmptyCommandResult
from pydomain.cqrs.saga.manager import SagaManager
from pydomain.cqrs.saga.registry import SagaRegistry
from pydomain.cqrs.saga.saga import Saga
from pydomain.cqrs.saga.state import SagaState, SagaStatus
from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.exceptions import ConcurrencyError
from pydomain.testing.fake_saga_repository import FakeSagaRepository

logging.basicConfig(level=logging.CRITICAL)


class OrderCreated(DomainEvent):
    order_id: str

class ItemsReserved(DomainEvent):
    order_id: str

class PaymentDeclined(DomainEvent):
    order_id: str

class ApprovalGranted(DomainEvent):
    order_id: str

class ReserveItems(Command[EmptyCommandResult]):
    order_id: str

class CancelReservation(Command[EmptyCommandResult]):
    order_id: str

class ChargePayment(Command[EmptyCommandResult]):
    order_id: str

class RefundPayment(Command[EmptyCommandResult]):
    order_id: str

class NotifyCustomer(Command[EmptyCommandResult]):
    order_id: str

class ConfirmOrder(Command[EmptyCommandResult]):
    order_id: str


class RecordingBus:
    """Duck-typed command bus: records every dispatch; can fail chosen types."""
    def __init__(self, fail_types: set[str] | None = None, fail_times: int | None = None) -> None:
        self.sent: list[str] = []
        self.sent_cmds: list[Command[Any]] = []
        self.fail_types = fail_types or set()
        self.fail_times = fail_times  # None = always
        self.failures = 0

    async def dispatch(self, cmd: Command[Any]) -> Any:
        name = type(cmd).__name__
        if name in self.fail_types and (self.fail_times is None or self.failures < self.fail_times):
            self.failures += 1
            self.sent.append(f"{name}!FAILED")
            raise RuntimeError(f"bus refused {name}")
        self.sent.append(name)
        self.sent_cmds.append(cmd)
        return None


class VersionCheckingSagaRepo(FakeSagaRepository):
    """FakeSagaRepository + the FakeRepository OCC convention
    (stored.version must equal state.version; repo bumps on update)."""
    def __init__(self) -> None:
        super().__init__()
        self.conflicts: list[str] = []

    async def save(self, state: SagaState) -> None:
        existing = self._store.get(state.id)
        if existing is not None:
            if existing.version != state.version:
                self.conflicts.append(f"stored={existing.version} given={state.version}")
                raise ConcurrencyError(
                    f"expected {state.version}, found {existing.version}")
            state.version += 1
        await super().save(state)


class SaveCountingRepo(FakeSagaRepository):
    """Crash injection: raises SystemExit-like error at the Nth save."""
    def __init__(self, crash_at: int | None = None) -> None:
        super().__init__()
        self.n = 0
        self.crash_at = crash_at

    async def save(self, state: SagaState) -> None:
        self.n += 1
        if self.crash_at is not None and self.n == self.crash_at:
            raise KeyboardInterrupt("simulated crash")  # not an Exception subclass
        await super().save(state)


def make_manager(*saga_classes, bus=None, repo=None):
    reg = SagaRegistry()
    for c in saga_classes:
        reg.register_saga(c)
    bus = bus or RecordingBus()
    repo = repo or FakeSagaRepository()
    return SagaManager(repository=repo, registry=reg, command_bus=bus), bus, repo


async def only_state(repo: FakeSagaRepository) -> SagaState:
    states = list(repo._store.values())  # probe-only peek
    assert len(states) == 1, len(states)
    return states[0]
