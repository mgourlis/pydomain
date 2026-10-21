"""DCE-120 (W1-5): declarative failure and recovery losses (ADR-077).

What reaches the command bus, scenario by scenario, for the audit rows
SAGA-01, SAGA-11b, SAGA-11c, SAGA-21 and FAKE-12. Each test observes the
bus, the repository's public queries and ``pull_events()``.
"""

from __future__ import annotations

import contextlib
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pydomain.cqrs.commands import Command
from pydomain.cqrs.saga.manager import SagaManager
from pydomain.cqrs.saga.registry import SagaRegistry
from pydomain.cqrs.saga.saga import Saga
from pydomain.cqrs.saga.state import SagaState, SagaStatus
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing.fake_saga_repository import FakeSagaRepository

from .conftest import (
    CancelPayment,
    CancelReservation,
    ConfirmOrder,
    ItemsReserved,
    NotifyCustomerOfCancellation,
    OrderCreated,
    PaymentDeclined,
    ProcessPayment,
    ReserveItems,
)


class RecordingBus:
    """Records every dispatch by command type; refuses the types in ``down``."""

    def __init__(self, down: set[type[Command[Any]]] | None = None) -> None:
        self.down: set[type[Command[Any]]] = down or set()
        self.sent: list[str] = []
        self.refused: list[str] = []

    async def dispatch(self, command: Command[Any]) -> None:
        if type(command) in self.down:
            self.refused.append(type(command).__name__)
            raise RuntimeError("downstream unavailable")
        self.sent.append(type(command).__name__)


class PerOrderBus:
    """Records dispatches by ``order_id``; refuses the orders in ``down``."""

    def __init__(self, down: set[str]) -> None:
        self.down = down
        self.sent: list[str] = []

    async def dispatch(self, command: Any) -> None:
        if command.order_id in self.down:
            raise RuntimeError("downstream unavailable")
        self.sent.append(command.order_id)


def _manager(
    saga_class: type[Saga[Any]], bus: Any, repo: FakeSagaRepository | None = None
) -> tuple[SagaManager, FakeSagaRepository]:
    registry = SagaRegistry()
    registry.register_saga(saga_class)
    repository = repo if repo is not None else FakeSagaRepository()
    manager = SagaManager(repository=repository, registry=registry, command_bus=bus)
    return manager, repository


# ═══════════════════════════════════════════════════════════════════════
# SAGA-01: fail=True sends its forward command
# ═══════════════════════════════════════════════════════════════════════


class ChargeThenFailSaga(Saga[SagaState]):
    """Reserve, then on a declined payment charge-and-fail (synthetic)."""

    listens_to = [OrderCreated, PaymentDeclined]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            step="reserving",
            compensate=lambda e: CancelReservation(order_id=e.order_id),
        )
        self.on(
            PaymentDeclined,
            send=lambda e: ProcessPayment(order_id=e.order_id),
            step="charging",
            compensate=lambda e: CancelPayment(order_id=e.order_id),
            fail=True,
        )


class NotifyThenFailSaga(Saga[SagaState]):
    """ADR-055's motivating example: notify the customer, then fail."""

    listens_to = [OrderCreated, PaymentDeclined]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            step="reserving",
            compensate=lambda e: CancelReservation(order_id=e.order_id),
        )
        self.on(
            PaymentDeclined,
            send=lambda e: NotifyCustomerOfCancellation(customer_id=e.order_id),
            fail=True,
            fail_reason="payment declined",
        )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-120 SAGA-01: execute_compensations() discards the forward command",
)
@pytest.mark.anyio
async def test_fail_true_dispatches_forward_command_before_compensating() -> None:
    """Given a step with ``fail=True`` and a compensation stack,
    when its event arrives, then its forward command is sent first and the
    compensations follow in LIFO order."""
    bus = RecordingBus()
    manager, _ = _manager(ChargeThenFailSaga, bus)
    cid = uuid4()

    await manager.handle(OrderCreated(order_id="ORD-1", correlation_id=cid))
    await manager.handle(PaymentDeclined(order_id="ORD-1", correlation_id=cid))

    assert bus.sent == [
        "ReserveItems",
        "ProcessPayment",
        "CancelPayment",
        "CancelReservation",
    ]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-120 SAGA-01: ADR-055's own example loses its notification",
)
@pytest.mark.anyio
async def test_adr_055_example_sends_its_notification_before_compensating() -> None:
    """Given ADR-055's notify-then-fail step, the notification is sent before
    the earlier step is compensated."""
    bus = RecordingBus()
    manager, repo = _manager(NotifyThenFailSaga, bus)
    cid = uuid4()

    await manager.handle(OrderCreated(order_id="ORD-2", correlation_id=cid))
    await manager.handle(PaymentDeclined(order_id="ORD-2", correlation_id=cid))

    assert bus.sent == [
        "ReserveItems",
        "NotifyCustomerOfCancellation",
        "CancelReservation",
    ]
    state = await repo.find_by_correlation_id(cid, "NotifyThenFailSaga")
    assert state is not None
    assert state.status == SagaStatus.COMPENSATED
    assert state.error == "payment declined"


# ═══════════════════════════════════════════════════════════════════════
# SAGA-11b / SAGA-11c: retry counting and max_retries
# ═══════════════════════════════════════════════════════════════════════


class ReserveThenConfirmSaga(Saga[SagaState]):
    """Reserve on OrderCreated, confirm on ItemsReserved (synthetic)."""

    listens_to = [OrderCreated, ItemsReserved]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            step="reserving",
            compensate=lambda e: CancelReservation(order_id=e.order_id),
        )
        self.on(ItemsReserved, send=lambda e: ConfirmOrder(order_id=e.order_id))


class TenRetriesState(SagaState):
    max_retries: int = 10


class TenRetriesSaga(ReserveThenConfirmSaga):
    state_class = TenRetriesState


class NoRetriesState(SagaState):
    max_retries: int = 0


class NoRetriesSaga(ReserveThenConfirmSaga):
    state_class = NoRetriesState


async def _state(repo: FakeSagaRepository, cid: Any, saga_type: str) -> SagaState:
    state = await repo.find_by_correlation_id(cid, saga_type)
    assert state is not None
    return state


@pytest.mark.anyio
async def test_retry_count_increments_once_per_failure() -> None:
    """Given a forward command whose downstream stays down, each failed
    dispatch, first on the handle path and then on each recovery, adds
    exactly one to ``retry_count``."""
    bus = RecordingBus(down={ReserveItems})
    manager, repo = _manager(TenRetriesSaga, bus)
    cid = uuid4()

    await manager.handle(OrderCreated(order_id="ORD-3", correlation_id=cid))
    counts = [(await _state(repo, cid, "TenRetriesSaga")).retry_count]
    for _ in range(3):
        # Whether recovery re-raises the dispatch failure is SAGA-21's
        # concern; this test only counts.
        with contextlib.suppress(Exception):
            await manager.recover_pending_sagas()
        counts.append((await _state(repo, cid, "TenRetriesSaga")).retry_count)

    assert bus.refused == ["ReserveItems"] * 4
    assert counts == [1, 2, 3, 4]


@pytest.mark.anyio
async def test_max_retries_zero_still_sends_the_first_command() -> None:
    """``max_retries=0`` allows the first attempt: only retries are refused."""
    bus = RecordingBus()
    manager, repo = _manager(NoRetriesSaga, bus)
    cid = uuid4()

    await manager.handle(OrderCreated(order_id="ORD-4", correlation_id=cid))

    assert bus.sent == ["ReserveItems"]
    assert (await _state(repo, cid, "NoRetriesSaga")).status == SagaStatus.RUNNING


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-120 SAGA-11c: max_retries=0 means unlimited on the handle path",
)
@pytest.mark.anyio
async def test_max_retries_zero_fails_on_the_handle_path_after_one_failure() -> None:
    """Given ``max_retries=0`` and one failed dispatch, the next event finds
    the retries exhausted and fails the saga (B-49)."""
    bus = RecordingBus(down={ReserveItems})
    manager, repo = _manager(NoRetriesSaga, bus)
    cid = uuid4()

    await manager.handle(OrderCreated(order_id="ORD-5", correlation_id=cid))
    await manager.handle(ItemsReserved(order_id="ORD-5", correlation_id=cid))

    state = await _state(repo, cid, "NoRetriesSaga")
    assert state.status == SagaStatus.FAILED
    assert bus.sent == []


@pytest.mark.anyio
async def test_max_retries_zero_does_not_retry_on_recovery() -> None:
    """Given ``max_retries=0`` and one failed dispatch, recovery does not
    re-send the command and ends the saga (B-49)."""
    bus = RecordingBus(down={ReserveItems})
    manager, repo = _manager(NoRetriesSaga, bus)
    cid = uuid4()
    await manager.handle(OrderCreated(order_id="ORD-6", correlation_id=cid))
    bus.down.clear()

    await manager.recover_pending_sagas()

    state = await _state(repo, cid, "NoRetriesSaga")
    assert "ReserveItems" not in bus.sent
    assert state.is_terminal


@pytest.mark.xfail(
    strict=True,
    raises=ValidationError,
    reason="DCE-120 SAGA-11c: max_retries does not accept None (unlimited)",
)
@pytest.mark.anyio
async def test_max_retries_none_retries_without_limit() -> None:
    """``max_retries=None`` never gives up: recovery re-sends the command
    however many failures came before (B-49)."""
    bus = RecordingBus()
    manager, repo = _manager(ReserveThenConfirmSaga, bus)
    state = SagaState(
        saga_type="ReserveThenConfirmSaga",
        correlation_id=uuid4(),
        status=SagaStatus.SUSPENDED,
        retry_count=50,
        max_retries=None,  # pyright: ignore[reportArgumentType]
        pending_commands=[
            {
                "command_type": "ReserveItems",
                "module_name": ReserveItems.__module__,
                "data": {"order_id": "ORD-7", "item_count": 1},
                "dispatched": False,
            }
        ],
    )
    await repo.save(state)

    await manager.recover_pending_sagas()

    assert bus.sent == ["ReserveItems"]
    assert not (await repo.get_by_id(state.id)).is_terminal  # type: ignore[union-attr]


# ═══════════════════════════════════════════════════════════════════════
# SAGA-21: recovery continues past a failing saga
# ═══════════════════════════════════════════════════════════════════════


class ReserveOnlySaga(Saga[SagaState]):
    listens_to = [OrderCreated]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(OrderCreated, send=lambda e: ReserveItems(order_id=e.order_id))


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-120 SAGA-21: the first failing saga aborts the recovery batch",
)
@pytest.mark.anyio
async def test_recovery_continues_past_a_failing_saga() -> None:
    """Given two stalled sagas where only the first one's downstream is
    still down, one recovery run re-sends the second one's command."""
    bus = PerOrderBus(down={"ORD-A", "ORD-B"})
    manager, _ = _manager(ReserveOnlySaga, bus)
    await manager.handle(OrderCreated(order_id="ORD-A", correlation_id=uuid4()))
    await manager.handle(OrderCreated(order_id="ORD-B", correlation_id=uuid4()))
    bus.down = {"ORD-A"}

    # How recovery reports the saga it could not recover is decided
    # separately; this test asserts that it carries on.
    with contextlib.suppress(Exception):
        await manager.recover_pending_sagas()

    assert bus.sent == ["ORD-B"]


# ═══════════════════════════════════════════════════════════════════════
# FAKE-12: FakeSagaRepository drains events before it copies
# ═══════════════════════════════════════════════════════════════════════


class OrderNoted(DomainEvent):
    order_id: str


@pytest.mark.anyio
async def test_fake_saga_repository_does_not_re_emit_events() -> None:
    """Given a state saved with one event, loading and saving it again with
    no new event hands no event to ``pull_events()``."""
    repo = FakeSagaRepository()
    state = SagaState(saga_type="OrderSaga", correlation_id=uuid4())
    state._add_event(OrderNoted(order_id="ORD-8"))  # pyright: ignore[reportPrivateUsage]
    await repo.save(state)
    assert len(repo.pull_events()) == 1

    loaded = await repo.get_by_id(state.id)
    assert loaded is not None
    await repo.save(loaded)

    assert repo.pull_events() == []
