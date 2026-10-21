"""DCE-120 (W1-5): declarative failure and recovery losses (ADR-077).

What reaches the command bus, scenario by scenario, for the audit rows
SAGA-01, SAGA-11a to SAGA-11c, SAGA-19, SAGA-21 and FAKE-12, as the author
decided them (decision log B-49, B-83 to B-87). Each test observes the bus,
the logs, the repository's public queries and ``pull_events()``.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

import pytest

from pydomain.cqrs.command_bus import CommandBus
from pydomain.cqrs.commands import Command, EmptyCommandResult
from pydomain.cqrs.saga.manager import SagaManager
from pydomain.cqrs.saga.registry import SagaRegistry
from pydomain.cqrs.saga.saga import Saga
from pydomain.cqrs.saga.state import SagaState, SagaStatus
from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.testing import FakeUnitOfWork
from pydomain.testing.fake_repository import FakeRepository
from pydomain.testing.fake_saga_repository import FakeSagaRepository
from tests.support.content_sentinel import CONTENT_SENTINEL, assert_absent_from_logs

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
        self.sent_commands: list[Command[Any]] = []
        self.refused: list[str] = []

    async def dispatch(self, command: Command[Any]) -> None:
        if type(command) in self.down:
            self.refused.append(type(command).__name__)
            raise RuntimeError(CONTENT_SENTINEL)
        self.sent.append(type(command).__name__)
        self.sent_commands.append(command)


class PerOrderBus:
    """Records dispatches by ``order_id``; refuses the orders in ``down``."""

    def __init__(self, down: set[str]) -> None:
        self.down = down
        self.sent: list[str] = []

    async def dispatch(self, command: Any) -> None:
        if command.order_id in self.down:
            raise RuntimeError(CONTENT_SENTINEL)
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


class ImperativeChargeThenFailSaga(Saga[SagaState]):
    """ADR-055's imperative pattern: ``dispatch()`` then ``await fail()``."""

    listens_to = [OrderCreated, PaymentDeclined]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            compensate=lambda e: CancelReservation(order_id=e.order_id),
        )
        self.on(PaymentDeclined, handler=self.charge_then_fail)

    async def charge_then_fail(self, event: DomainEvent) -> None:
        assert isinstance(event, PaymentDeclined)
        self.dispatch(ProcessPayment(order_id=event.order_id))
        await self.fail("payment declined", compensate=True)


class RaiseAfterDispatchSaga(Saga[SagaState]):
    """Queues a command, then its handler raises."""

    listens_to = [OrderCreated, PaymentDeclined]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            compensate=lambda e: CancelReservation(order_id=e.order_id),
        )
        self.on(PaymentDeclined, handler=self.charge_then_raise)

    async def charge_then_raise(self, event: DomainEvent) -> None:
        assert isinstance(event, PaymentDeclined)
        self.dispatch(ProcessPayment(order_id=event.order_id))
        raise ValueError("synthetic handler failure")


@pytest.mark.anyio
async def test_imperative_dispatch_then_fail_sends_the_command_first() -> None:
    """ADR-055's imperative pattern sends its command before compensating (B-83)."""
    bus = RecordingBus()
    manager, _ = _manager(ImperativeChargeThenFailSaga, bus)
    cid = uuid4()

    await manager.handle(OrderCreated(order_id="ORD-11", correlation_id=cid))
    await manager.handle(PaymentDeclined(order_id="ORD-11", correlation_id=cid))

    assert bus.sent == ["ReserveItems", "ProcessPayment", "CancelReservation"]


@pytest.mark.anyio
async def test_forward_command_on_fail_carries_the_event_as_causation() -> None:
    """The forward command sent on failure is caused by the step's event (B-83)."""
    bus = RecordingBus()
    manager, repo = _manager(ChargeThenFailSaga, bus)
    cid = uuid4()
    declined = PaymentDeclined(order_id="ORD-12", correlation_id=cid)

    await manager.handle(OrderCreated(order_id="ORD-12", correlation_id=cid))
    await manager.handle(declined)

    charges = [c for c in bus.sent_commands if isinstance(c, ProcessPayment)]
    assert len(charges) == 1
    assert charges[0].causation_id == declined.event_id
    assert charges[0].correlation_id == cid


@pytest.mark.anyio
async def test_failing_forward_command_is_recorded_and_compensations_continue() -> None:
    """Given a ``fail=True`` step whose forward command is refused, the
    failure is recorded in ``failed_compensations`` by type only, the
    compensations are still sent, and the saga ends ``FAILED`` (B-83)."""
    bus = RecordingBus(down={ProcessPayment})
    manager, repo = _manager(ChargeThenFailSaga, bus)
    cid = uuid4()

    await manager.handle(OrderCreated(order_id="ORD-13", correlation_id=cid))
    await manager.handle(PaymentDeclined(order_id="ORD-13", correlation_id=cid))

    state = await _state(repo, cid, "ChargeThenFailSaga")
    assert bus.refused == ["ProcessPayment"]
    assert bus.sent == ["ReserveItems", "CancelPayment", "CancelReservation"]
    assert state.status == SagaStatus.FAILED
    failed = [(f["command_type"], f["error"]) for f in state.failed_compensations]
    assert failed == [("ProcessPayment", "RuntimeError")]


@pytest.mark.anyio
async def test_commands_queued_before_a_handler_raises_are_not_sent() -> None:
    """A command queued by a handler that then raises stays unsent; the
    earlier steps are compensated (B-83; checkpointing it is SAGA-12, W2-5)."""
    bus = RecordingBus()
    manager, _ = _manager(RaiseAfterDispatchSaga, bus)
    cid = uuid4()

    await manager.handle(OrderCreated(order_id="ORD-14", correlation_id=cid))
    await manager.handle(PaymentDeclined(order_id="ORD-14", correlation_id=cid))

    assert bus.sent == ["ReserveItems", "CancelReservation"]


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


class ThreeRetriesSaga(ReserveThenConfirmSaga):
    """Uses the default ``max_retries=3``."""


@pytest.mark.anyio
async def test_max_retries_three_allows_exactly_three_retries() -> None:
    """With ``max_retries=3`` the command is tried once and retried three
    times; the next recovery ends the saga (B-85)."""
    bus = RecordingBus(down={ReserveItems})
    manager, repo = _manager(ThreeRetriesSaga, bus)
    cid = uuid4()

    await manager.handle(OrderCreated(order_id="ORD-15", correlation_id=cid))
    for _ in range(5):
        await manager.recover_pending_sagas()

    assert bus.refused == ["ReserveItems"] * 4
    assert (await _state(repo, cid, "ThreeRetriesSaga")).is_terminal


# ═══════════════════════════════════════════════════════════════════════
# SAGA-11a: an unhydratable pending command stays recorded
# ═══════════════════════════════════════════════════════════════════════


def _pending(command_type: str, module_name: str, order_id: str) -> dict[str, Any]:
    return {
        "command_type": command_type,
        "module_name": module_name,
        "data": {"order_id": order_id, "item_count": 1},
        "dispatched": False,
    }


async def _stalled_with_unhydratable(
    repo: FakeSagaRepository, *, retry_count: int = 1, max_retries: int = 3
) -> SagaState:
    state = SagaState(
        saga_type="ReserveThenConfirmSaga",
        correlation_id=uuid4(),
        status=SagaStatus.SUSPENDED,
        retry_count=retry_count,
        max_retries=max_retries,
        pending_commands=[
            _pending("ReserveItems", "orders.renamed_module", CONTENT_SENTINEL),
            _pending("ReserveItems", ReserveItems.__module__, "ORD-16"),
        ],
    )
    await repo.save(state)
    return state


@pytest.mark.anyio
async def test_unhydratable_pending_command_is_recorded_not_deleted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Given a stalled saga whose first pending command cannot be hydrated,
    recovery sends the other one, keeps the unhydratable one unsent in
    ``pending_commands``, counts one failed attempt, suspends the saga with
    the command's type as the reason and logs only the type and the saga
    id (B-84)."""
    bus = RecordingBus()
    manager, repo = _manager(ReserveThenConfirmSaga, bus)
    state = await _stalled_with_unhydratable(repo)

    with caplog.at_level(logging.DEBUG, logger="pydomain"):
        not_recovered = await manager.recover_pending_sagas()

    stored = await repo.get_by_id(state.id)
    assert stored is not None
    assert bus.sent == ["ReserveItems"]
    left = [p for p in stored.pending_commands if not p["dispatched"]]
    assert [p["module_name"] for p in left] == ["orders.renamed_module"]
    assert stored.retry_count == 2
    assert stored.status == SagaStatus.SUSPENDED
    assert stored.suspension_reason == "Could not hydrate ReserveItems"
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any(
        "ReserveItems" in r.getMessage() and str(state.id) in r.getMessage()
        for r in errors
    )
    assert_absent_from_logs(caplog)
    assert not_recovered == [state.id]


@pytest.mark.anyio
async def test_unhydratable_command_is_sent_once_its_import_path_works() -> None:
    """After the import path is fixed, the next recovery sends the command
    and clears the pending list (B-84)."""
    bus = RecordingBus()
    manager, repo = _manager(ReserveThenConfirmSaga, bus)
    state = await _stalled_with_unhydratable(repo)
    await manager.recover_pending_sagas()

    stored = await repo.get_by_id(state.id)
    assert stored is not None
    for entry in stored.pending_commands:
        entry["module_name"] = ReserveItems.__module__
    await repo.save(stored)
    not_recovered = await manager.recover_pending_sagas()

    recovered = await repo.get_by_id(state.id)
    assert recovered is not None
    assert bus.sent == ["ReserveItems", "ReserveItems"]
    assert recovered.pending_commands == []
    assert recovered.retry_count == 0
    assert not_recovered == []


@pytest.mark.anyio
async def test_unhydratable_command_fails_the_saga_when_retries_run_out() -> None:
    """An unhydratable command exhausts ``max_retries`` like any failing
    command: with one retry allowed, the first recovery retries it and the
    second ends the saga (B-84, B-85)."""
    bus = RecordingBus()
    manager, repo = _manager(ReserveThenConfirmSaga, bus)
    state = await _stalled_with_unhydratable(repo, retry_count=1, max_retries=1)

    await manager.recover_pending_sagas()
    retried = await repo.get_by_id(state.id)
    assert retried is not None
    assert not retried.is_terminal  # one retry is allowed
    await manager.recover_pending_sagas()

    stored = await repo.get_by_id(state.id)
    assert stored is not None
    assert stored.is_terminal


# ═══════════════════════════════════════════════════════════════════════
# SAGA-21: recovery continues past a failing saga
# ═══════════════════════════════════════════════════════════════════════


class ReserveOnlySaga(Saga[SagaState]):
    listens_to = [OrderCreated]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(OrderCreated, send=lambda e: ReserveItems(order_id=e.order_id))


@pytest.mark.anyio
async def test_recovery_continues_past_a_failing_saga(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Given two stalled sagas where only the first one's downstream is
    still down, one recovery run re-sends the second one's command, logs
    the first one's failure by type, and returns the first one's id (B-86)."""
    bus = PerOrderBus(down={"ORD-A", "ORD-B"})
    manager, repo = _manager(ReserveOnlySaga, bus)
    cid_a = uuid4()
    await manager.handle(OrderCreated(order_id="ORD-A", correlation_id=cid_a))
    await manager.handle(OrderCreated(order_id="ORD-B", correlation_id=uuid4()))
    bus.down = {"ORD-A"}
    saga_a = await _state(repo, cid_a, "ReserveOnlySaga")
    caplog.clear()  # only the recovery run's records are checked

    with caplog.at_level(logging.DEBUG, logger="pydomain"):
        not_recovered = await manager.recover_pending_sagas()

    assert bus.sent == ["ORD-B"]
    assert not_recovered == [saga_a.id]
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any(str(saga_a.id) in m and "RuntimeError" in m for m in errors)
    assert_absent_from_logs(caplog)


@pytest.mark.anyio
async def test_recovery_returns_no_ids_when_every_saga_recovers() -> None:
    """A run that recovers every stalled saga returns an empty list (B-86)."""
    bus = PerOrderBus(down={"ORD-C"})
    manager, _ = _manager(ReserveOnlySaga, bus)
    await manager.handle(OrderCreated(order_id="ORD-C", correlation_id=uuid4()))
    bus.down = set()

    assert await manager.recover_pending_sagas() == []
    assert bus.sent == ["ORD-C"]


# ═══════════════════════════════════════════════════════════════════════
# SAGA-19: the manager needs a dispatcher that publishes events
# ═══════════════════════════════════════════════════════════════════════


class Reservation(AggregateRoot[Any]):
    order_id: str = ""


class ReserveThenCompleteSaga(Saga[SagaState]):
    listens_to = [OrderCreated, ItemsReserved]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(OrderCreated, send=lambda e: ReserveItems(order_id=e.order_id))
        self.on(
            ItemsReserved,
            send=lambda e: ConfirmOrder(order_id=e.order_id),
            complete=True,
        )


def _register_handlers(register: Any) -> list[str]:
    """Register handlers whose ReserveItems raises ItemsReserved."""
    repository = FakeRepository[Reservation, Any]()
    confirmed: list[str] = []

    async def reserve(command: Any, uow: Any) -> EmptyCommandResult:
        reservation = Reservation(id=uuid4(), order_id=command.order_id)
        reservation._add_event(  # pyright: ignore[reportPrivateUsage]
            ItemsReserved(order_id=command.order_id)
        )
        await repository.save(reservation)
        return EmptyCommandResult()

    async def confirm(command: Any, uow: Any) -> EmptyCommandResult:
        confirmed.append(command.order_id)
        return EmptyCommandResult()

    register(ReserveItems, reserve, lambda: FakeUnitOfWork(repository))
    register(ConfirmOrder, confirm, lambda: FakeUnitOfWork(repository))
    return confirmed


def _refusal(command_bus: Any) -> str | None:
    registry = SagaRegistry()
    registry.register_saga(ReserveThenCompleteSaga)
    try:
        SagaManager(FakeSagaRepository(), registry, command_bus)
    except TypeError as error:
        return str(error)
    return None


@pytest.mark.anyio
async def test_saga_events_reach_handlers_through_dispatcher() -> None:
    """Wired to a ``MessageBus``, the events a saga's command raises reach
    the saga, which sends its next command; a bare ``CommandBus``, which
    does not publish them, is refused (B-87)."""
    bus = MessageBus()
    confirmed = _register_handlers(bus.register_command)
    registry = SagaRegistry()
    registry.register_saga(ReserveThenCompleteSaga)
    manager = SagaManager(FakeSagaRepository(), registry, bus)
    manager.bind_to(bus)

    await bus.dispatch(OrderCreated(order_id="ORD-17", correlation_id=uuid4()))

    # The saga saw ItemsReserved: it sent ConfirmOrder. Its final saved
    # status is SAGA-20's re-entrant lost update (W2-5), not checked here.
    assert confirmed == ["ORD-17"]
    assert _refusal(CommandBus()) is not None


def test_bare_command_bus_refusal_says_to_pass_the_message_bus() -> None:
    """The ``TypeError`` names the ``MessageBus`` as the fix (B-87)."""
    message = _refusal(CommandBus())
    assert message is not None
    assert "MessageBus" in message
    assert "a CommandBus returns them instead" in message


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


# ═══════════════════════════════════════════════════════════════════════
# Hardening: what recover_pending_sagas reports, per kind of stalled saga
# ═══════════════════════════════════════════════════════════════════════


def _stalled(saga_type: str, **fields: Any) -> SagaState:
    return SagaState(saga_type=saga_type, correlation_id=uuid4(), **fields)


@pytest.mark.anyio
async def test_recovered_compensation_is_not_reported() -> None:
    """A finished compensation counts as recovered (B-86)."""
    bus = RecordingBus()
    manager, repo = _manager(ReserveThenConfirmSaga, bus)
    await repo.save(_stalled("ReserveThenConfirmSaga", status=SagaStatus.COMPENSATING))

    assert await manager.recover_pending_sagas() == []


@pytest.mark.anyio
async def test_saga_with_only_dispatched_commands_is_not_reported() -> None:
    """Pending commands that were all dispatched are cleared, not reported."""
    bus = RecordingBus()
    manager, repo = _manager(ReserveThenConfirmSaga, bus)
    entry = _pending("ReserveItems", ReserveItems.__module__, "ORD-18")
    entry["dispatched"] = True
    state = _stalled(
        "ReserveThenConfirmSaga", status=SagaStatus.RUNNING, pending_commands=[entry]
    )
    await repo.save(state)

    assert await manager.recover_pending_sagas() == []
    stored = await repo.get_by_id(state.id)
    assert stored is not None
    assert stored.pending_commands == []
    assert bus.sent == []


@pytest.mark.anyio
async def test_saga_failed_for_exhausted_retries_is_not_reported() -> None:
    """A saga recovery ends for exhausted retries counts as handled (B-85, B-86)."""
    bus = RecordingBus()
    manager, repo = _manager(ReserveThenConfirmSaga, bus)
    state = _stalled(
        "ReserveThenConfirmSaga",
        status=SagaStatus.SUSPENDED,
        retry_count=4,
        pending_commands=[_pending("ReserveItems", ReserveItems.__module__, "ORD-19")],
    )
    await repo.save(state)

    assert await manager.recover_pending_sagas() == []
    stored = await repo.get_by_id(state.id)
    assert stored is not None
    assert stored.is_terminal
    assert bus.sent == []


@pytest.mark.anyio
async def test_unregistered_saga_type_is_reported_and_recovery_continues() -> None:
    """An unregistered saga type is reported, and later sagas still recover."""
    bus = RecordingBus()
    manager, repo = _manager(ReserveThenConfirmSaga, bus)
    unknown = _stalled(
        "RetiredSaga",
        status=SagaStatus.SUSPENDED,
        pending_commands=[_pending("ReserveItems", ReserveItems.__module__, "ORD-20")],
    )
    known = _stalled(
        "ReserveThenConfirmSaga",
        status=SagaStatus.SUSPENDED,
        pending_commands=[_pending("ReserveItems", ReserveItems.__module__, "ORD-21")],
    )
    await repo.save(unknown)
    await repo.save(known)

    assert await manager.recover_pending_sagas() == [unknown.id]
    assert bus.sent == ["ReserveItems"]


@pytest.mark.anyio
async def test_unhydratable_compensation_is_recorded_with_its_data() -> None:
    """A compensation that cannot be hydrated is recorded with its type,
    module and stored data, and a value-free error."""
    state = SagaState(saga_type="ChargeThenFailSaga", correlation_id=uuid4())
    saga = ChargeThenFailSaga(state)
    saga.add_compensation(CancelReservation(order_id="ORD-22"))
    state.compensation_stack[0] = state.compensation_stack[0].model_copy(
        update={"module_name": "orders.renamed_module"}
    )

    await saga.execute_compensations()

    assert state.failed_compensations == [
        {
            "command_type": "CancelReservation",
            "module_name": "orders.renamed_module",
            "data": {
                "command_id": state.failed_compensations[0]["data"]["command_id"],
                "correlation_id": None,
                "causation_id": None,
                "order_id": "ORD-22",
            },
            "error": "Could not hydrate from orders.renamed_module",
        }
    ]
