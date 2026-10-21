"""DCE-120 rework (PR #116): edge cases of saga failure and recovery (ADR-077).

The reviewers' probes P2 to P14, as tests: the timeout path, failures through a
real ``MessageBus``, the hydration suspension (B-91), ordered recovery (B-92),
``max_retries`` bounds (B-85, B-93) and the dispatcher check (B-87).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from pydomain.cqrs.command_bus import CommandBus
from pydomain.cqrs.commands import EmptyCommandResult
from pydomain.cqrs.saga.manager import SagaManager
from pydomain.cqrs.saga.registry import SagaRegistry
from pydomain.cqrs.saga.saga import Saga
from pydomain.cqrs.saga.state import SagaState, SagaStatus
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
    OrderCreated,
    PaymentDeclined,
    ProcessPayment,
    ReserveItems,
)
from .test_failure_and_recovery import (
    RecordingBus,
    Reservation,
    ReserveThenConfirmSaga,
    _manager,
    _pending,
    _state,
)

_GONE = "orders.renamed_module"


# ═══════════════════════════════════════════════════════════════════════
# The timeout path (P3, P14) and a raising handler without compensations (P2)
# ═══════════════════════════════════════════════════════════════════════


class TimeoutSaga(Saga[SagaState]):
    """Reserves, then waits; its timeout handler charges (synthetic)."""

    listens_to = [OrderCreated]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            compensate=lambda e: CancelReservation(order_id=e.order_id),
            suspend=True,
            suspend_timeout=timedelta(hours=1),
        )


class TimeoutDispatchThenRaiseSaga(TimeoutSaga):
    async def on_timeout(self) -> None:
        self.dispatch(ProcessPayment(order_id="ORD-31"))
        raise ValueError("synthetic timeout handler failure")


class TimeoutDispatchThenFailSaga(TimeoutSaga):
    async def on_timeout(self) -> None:
        self.dispatch(ProcessPayment(order_id="ORD-32"))
        await self.fail("approval timed out", compensate=True)


async def _expire(repo: FakeSagaRepository, cid: Any, saga_type: str) -> None:
    state = await _state(repo, cid, saga_type)
    state.timeout_at = datetime.now(UTC) - timedelta(seconds=1)
    await repo.save(state)


@pytest.mark.anyio
async def test_timeout_handler_that_raises_does_not_send_its_queued_command() -> None:
    """Given ``on_timeout()`` that queues a command and then raises, the
    saga is compensated and the queued command is not sent (B-83)."""
    bus = RecordingBus()
    manager, repo = _manager(TimeoutDispatchThenRaiseSaga, bus)
    cid = uuid4()
    await manager.handle(OrderCreated(order_id="ORD-31", correlation_id=cid))
    await _expire(repo, cid, "TimeoutDispatchThenRaiseSaga")

    await manager.process_timeouts()

    assert bus.sent == ["ReserveItems", "CancelReservation"]


@pytest.mark.anyio
async def test_timeout_dispatch_then_fail_sends_the_command_first() -> None:
    """``on_timeout()`` that dispatches and then fails sends the command
    before the compensations, caused by the saga id (no event) (B-83)."""
    bus = RecordingBus()
    manager, repo = _manager(TimeoutDispatchThenFailSaga, bus)
    cid = uuid4()
    await manager.handle(OrderCreated(order_id="ORD-32", correlation_id=cid))
    await _expire(repo, cid, "TimeoutDispatchThenFailSaga")

    await manager.process_timeouts()

    state = await _state(repo, cid, "TimeoutDispatchThenFailSaga")
    assert bus.sent == ["ReserveItems", "ProcessPayment", "CancelReservation"]
    charge = bus.sent_commands[1]
    assert charge.causation_id == state.id
    assert state.status == SagaStatus.COMPENSATED


class RaiseWithoutCompensationSaga(Saga[SagaState]):
    listens_to = [OrderCreated, PaymentDeclined]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(OrderCreated, send=lambda e: ReserveItems(order_id=e.order_id))
        self.on(PaymentDeclined, handler=self.charge_then_raise)

    async def charge_then_raise(self, event: Any) -> None:
        self.dispatch(ProcessPayment(order_id=event.order_id))
        raise ValueError("synthetic handler failure")


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="SAGA-12 (W2-5): with no compensation to run, a raising handler's "
    "queued command is still sent",
)
@pytest.mark.anyio
async def test_raising_handler_without_compensations_sends_nothing() -> None:
    """A handler that queues a command and then raises should send nothing,
    also when there is no compensation to run (SAGA-12, W2-5)."""
    bus = RecordingBus()
    manager, _ = _manager(RaiseWithoutCompensationSaga, bus)
    cid = uuid4()

    await manager.handle(OrderCreated(order_id="ORD-33", correlation_id=cid))
    await manager.handle(PaymentDeclined(order_id="ORD-33", correlation_id=cid))

    assert bus.sent == ["ReserveItems"]


# ═══════════════════════════════════════════════════════════════════════
# A failing forward command through a real MessageBus (P12, P13)
# ═══════════════════════════════════════════════════════════════════════


class ChargeThenFailSaga(Saga[SagaState]):
    listens_to = [OrderCreated, PaymentDeclined]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            compensate=lambda e: CancelReservation(order_id=e.order_id),
        )
        self.on(
            PaymentDeclined,
            send=lambda e: ProcessPayment(order_id=e.order_id),
            fail=True,
        )


def _message_bus_refusing_charges() -> MessageBus:
    bus = MessageBus()
    reservations = FakeRepository[Reservation, Any]()

    async def accept(command: Any, uow: Any) -> EmptyCommandResult:
        return EmptyCommandResult()

    async def refuse(command: Any, uow: Any) -> EmptyCommandResult:
        raise ConnectionError(CONTENT_SENTINEL)

    for command_type in (ReserveItems, CancelReservation):
        bus.register_command(command_type, accept, lambda: FakeUnitOfWork(reservations))
    bus.register_command(ProcessPayment, refuse, lambda: FakeUnitOfWork(reservations))
    return bus


async def _decline_through_message_bus() -> tuple[SagaState, FakeSagaRepository]:
    manager, repo = _manager(ChargeThenFailSaga, _message_bus_refusing_charges())
    cid = uuid4()
    await manager.handle(OrderCreated(order_id="ORD-34", correlation_id=cid))
    await manager.handle(PaymentDeclined(order_id="ORD-34", correlation_id=cid))
    return await _state(repo, cid, "ChargeThenFailSaga"), repo


@pytest.mark.anyio
async def test_failed_forward_command_records_the_handlers_error_type() -> None:
    """Through a ``MessageBus``, a refused forward command is recorded with
    its handler's own exception type, not ``CommandExecutionError`` (B-83)."""
    state, _ = await _decline_through_message_bus()

    failed = [(f["command_type"], f["error"]) for f in state.failed_compensations]
    assert failed == [("ProcessPayment", "ConnectionError")]
    assert state.status == SagaStatus.FAILED


@pytest.mark.anyio
async def test_failed_forward_command_is_logged_as_forward_by_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The failure is logged as a forward command's, naming the type only."""
    with caplog.at_level(logging.DEBUG, logger="pydomain"):
        await _decline_through_message_bus()

    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any(
        m.startswith("Forward command ProcessPayment failed") and "ConnectionError" in m
        for m in errors
    )
    assert not any(m.startswith("Compensation command ProcessPayment") for m in errors)
    assert_absent_from_logs(caplog)


# ═══════════════════════════════════════════════════════════════════════
# The hydration suspension (B-91) and ordered recovery (B-92)
# ═══════════════════════════════════════════════════════════════════════


class ApprovalSaga(Saga[SagaState]):
    """Resumes on ItemsReserved only from the "approval" step."""

    listens_to = [OrderCreated, ItemsReserved]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: ReserveItems(order_id=e.order_id),
            step="reserving",
        )
        self.on(
            ItemsReserved,
            send=lambda e: ConfirmOrder(order_id=e.order_id),
            resumes_from="approval",
        )


async def _fix_import_paths(repo: FakeSagaRepository, state: SagaState) -> None:
    stored = await repo.get_by_id(state.id)
    assert stored is not None
    for entry in stored.pending_commands:
        if entry["module_name"] == _GONE:
            entry["module_name"] = ReserveItems.__module__
    await repo.save(stored)


async def _saved(repo: FakeSagaRepository, **fields: Any) -> SagaState:
    fields.setdefault("saga_type", "ApprovalSaga")
    state = SagaState(correlation_id=uuid4(), current_step="reserving", **fields)
    await repo.save(state)
    return state


@pytest.mark.anyio
async def test_hydration_failure_keeps_an_earlier_suspension() -> None:
    """A saga already suspended keeps its reason and ``timeout_at`` when a
    pending command cannot be hydrated (B-91)."""
    manager, repo = _manager(ApprovalSaga, RecordingBus())
    timeout = datetime.now(UTC) + timedelta(hours=1)
    state = await _saved(
        repo,
        status=SagaStatus.SUSPENDED,
        suspension_reason="awaiting approval",
        timeout_at=timeout,
        pending_commands=[_pending("ReserveItems", _GONE, "ORD-35")],
    )

    assert await manager.recover_pending_sagas() == [state.id]

    stored = await repo.get_by_id(state.id)
    assert stored is not None
    assert stored.status == SagaStatus.SUSPENDED
    assert stored.suspension_reason == "awaiting approval"
    assert stored.timeout_at == timeout


@pytest.mark.anyio
async def test_saga_returns_to_running_once_the_unhydratable_command_is_sent() -> None:
    """A running saga suspended only for an unhydratable command is running
    again once recovery has sent it (B-91)."""
    bus = RecordingBus()
    manager, repo = _manager(ApprovalSaga, bus)
    state = await _saved(
        repo,
        status=SagaStatus.RUNNING,
        pending_commands=[_pending("ReserveItems", _GONE, "ORD-36")],
    )
    await manager.recover_pending_sagas()
    suspended = await repo.get_by_id(state.id)
    assert suspended is not None
    assert suspended.status == SagaStatus.SUSPENDED
    assert suspended.suspension_reason == "Could not hydrate ReserveItems"

    await _fix_import_paths(repo, state)
    assert await manager.recover_pending_sagas() == []

    stored = await repo.get_by_id(state.id)
    assert stored is not None
    assert bus.sent == ["ReserveItems"]
    assert stored.status == SagaStatus.RUNNING
    assert stored.suspension_reason is None
    assert stored.suspended_at is None


@pytest.mark.anyio
async def test_resumes_from_saga_handles_its_next_event_after_the_fix() -> None:
    """A ``resumes_from`` saga that was running is not left suspended at a
    step its next event may not resume from (B-91)."""
    bus = RecordingBus()
    manager, repo = _manager(ApprovalSaga, bus)
    state = await _saved(
        repo,
        status=SagaStatus.RUNNING,
        pending_commands=[_pending("ReserveItems", _GONE, "ORD-37")],
    )
    await manager.recover_pending_sagas()
    await _fix_import_paths(repo, state)
    await manager.recover_pending_sagas()

    assert state.correlation_id is not None
    await manager.handle(
        ItemsReserved(order_id="ORD-37", correlation_id=state.correlation_id)
    )

    assert bus.sent == ["ReserveItems", "ConfirmOrder"]


@pytest.mark.anyio
async def test_earlier_suspension_stays_after_the_unhydratable_command_is_sent() -> (
    None
):
    """A saga that was suspended before keeps that suspension once the
    command is sent: it returns to the status it had (B-91)."""
    bus = RecordingBus()
    manager, repo = _manager(ApprovalSaga, bus)
    state = await _saved(
        repo,
        status=SagaStatus.SUSPENDED,
        suspension_reason="awaiting approval",
        pending_commands=[_pending("ReserveItems", _GONE, "ORD-38")],
    )
    await manager.recover_pending_sagas()
    await _fix_import_paths(repo, state)
    await manager.recover_pending_sagas()

    stored = await repo.get_by_id(state.id)
    assert stored is not None
    assert bus.sent == ["ReserveItems"]
    assert stored.status == SagaStatus.SUSPENDED
    assert stored.suspension_reason == "awaiting approval"


@pytest.mark.anyio
async def test_recovery_stops_at_the_first_unhydratable_command() -> None:
    """Commands after an unhydratable one wait: nothing past the gap is sent,
    and the reason names the one command that stopped it (B-92)."""
    bus = RecordingBus()
    manager, repo = _manager(ApprovalSaga, bus)
    state = await _saved(
        repo,
        status=SagaStatus.RUNNING,
        pending_commands=[
            _pending("ReserveItems", ReserveItems.__module__, "ORD-39a"),
            _pending("ReserveItems", _GONE, "ORD-39b"),
            _pending("ReserveItems", _GONE, "ORD-39c"),
            _pending("ConfirmOrder", ConfirmOrder.__module__, "ORD-39d"),
        ],
    )

    await manager.recover_pending_sagas()

    stored = await repo.get_by_id(state.id)
    assert stored is not None
    assert [c.order_id for c in bus.sent_commands] == ["ORD-39a"]
    assert [p["dispatched"] for p in stored.pending_commands] == [
        True,
        False,
        False,
        False,
    ]
    assert stored.suspension_reason == "Could not hydrate ReserveItems"


@pytest.mark.anyio
async def test_recovery_keeps_the_pending_order_across_a_fixed_gap() -> None:
    """Once the gap is fixed, the waiting commands go out in their order (B-92)."""
    bus = RecordingBus()
    manager, repo = _manager(ApprovalSaga, bus)
    state = await _saved(
        repo,
        status=SagaStatus.RUNNING,
        pending_commands=[
            _pending("ReserveItems", _GONE, "ORD-40a"),
            _pending("ConfirmOrder", ConfirmOrder.__module__, "ORD-40b"),
        ],
    )
    await manager.recover_pending_sagas()
    await _fix_import_paths(repo, state)
    await manager.recover_pending_sagas()

    assert [c.order_id for c in bus.sent_commands] == ["ORD-40a", "ORD-40b"]


# ═══════════════════════════════════════════════════════════════════════
# max_retries bounds (B-85, B-93): property over both paths
# ═══════════════════════════════════════════════════════════════════════


def test_max_retries_rejects_negative_values() -> None:
    """``max_retries`` must be ``None`` or at least 0 (B-93)."""
    try:
        SagaState(saga_type="OrderSaga", max_retries=-1)
    except ValidationError as error:
        assert error.errors()[0]["loc"] == ("max_retries",)
    else:
        raise AssertionError("max_retries=-1 was accepted")


def _saga_for(limit: int | None) -> type[Saga[Any]]:
    class LimitedState(SagaState):
        max_retries: int | None = limit

    class LimitedSaga(ReserveThenConfirmSaga):
        state_class = LimitedState

    return LimitedSaga


async def _attempts_until_terminal(limit: int | None, path: str) -> int | None:
    bus = RecordingBus(down={ReserveItems})
    saga_class = _saga_for(limit)
    manager, repo = _manager(saga_class, bus)
    cid = uuid4()
    await manager.handle(OrderCreated(order_id="ORD-41", correlation_id=cid))
    for _ in range(8):
        if path == "recovery":
            await manager.recover_pending_sagas()
        else:
            await manager.handle(OrderCreated(order_id="ORD-41", correlation_id=cid))
        if (await _state(repo, cid, "LimitedSaga")).is_terminal:
            return len(bus.refused)
    return None


# Module-level, not a method: Hypothesis' differing_executors health check.
@given(
    limit=st.sampled_from([0, 1, 2, 3, None]),
    path=st.sampled_from(["recovery", "event"]),
)
def test_max_retries_allows_exactly_that_many_retries(
    limit: int | None, path: str
) -> None:
    """On either path the command is tried once plus ``max_retries`` times,
    then the saga ends; ``None`` never ends it (B-49, B-85)."""
    attempts = asyncio.run(_attempts_until_terminal(limit, path))

    assert attempts == (None if limit is None else limit + 1)


# ═══════════════════════════════════════════════════════════════════════
# The dispatcher check (B-87, P8)
# ═══════════════════════════════════════════════════════════════════════


class _CommandBusSubclass(CommandBus):
    pass


class _MessageBusSubclass(MessageBus):
    pass


class _WrappingDispatcher:
    """Publishes nothing itself; the manager cannot tell, and accepts it."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def dispatch(self, command: Any) -> Any:
        return await self.inner.dispatch(command)


@pytest.mark.parametrize(
    "dispatcher",
    [CommandBus(), _CommandBusSubclass()],
    ids=["CommandBus", "CommandBus subclass"],
)
def test_command_bus_and_its_subclasses_are_refused(dispatcher: Any) -> None:
    with pytest.raises(TypeError, match="MessageBus"):
        SagaManager(FakeSagaRepository(), SagaRegistry(), dispatcher)


@pytest.mark.parametrize(
    "dispatcher",
    [MessageBus(), _MessageBusSubclass(), _WrappingDispatcher(MessageBus())],
    ids=["MessageBus", "MessageBus subclass", "wrapping dispatcher"],
)
def test_other_dispatchers_are_accepted(dispatcher: Any) -> None:
    manager = SagaManager(FakeSagaRepository(), SagaRegistry(), dispatcher)
    assert manager.command_bus is dispatcher


# ═══════════════════════════════════════════════════════════════════════
# A mixed recovery batch (P9) and re-entry mid-compensation (P11)
# ═══════════════════════════════════════════════════════════════════════


class FailRefusingSaga(ReserveThenConfirmSaga):
    """Its ``fail()`` raises, so recovery of an exhausted one fails."""

    async def fail(self, reason: str, *, compensate: bool = True) -> None:
        raise RuntimeError(CONTENT_SENTINEL)


@pytest.mark.anyio
async def test_mixed_recovery_batch_reports_each_unrecovered_saga(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One run over a dispatch failure, an exhausted saga whose failing
    raises, an unregistered type, an unhydratable command and a healthy saga:
    only the healthy one recovers, and no log line carries content (B-86)."""
    bus = RecordingBus(down={ConfirmOrder})
    registry = SagaRegistry()
    registry.register_saga(ReserveThenConfirmSaga)
    registry.register_saga(FailRefusingSaga)
    repo = FakeSagaRepository()
    manager = SagaManager(repository=repo, registry=registry, command_bus=bus)

    def stalled(saga_type: str, entry: dict[str, Any], **fields: Any) -> SagaState:
        return SagaState(
            saga_type=saga_type,
            correlation_id=uuid4(),
            status=SagaStatus.RUNNING,
            pending_commands=[entry],
            **fields,
        )

    down = stalled(
        "ReserveThenConfirmSaga",
        _pending("ConfirmOrder", ConfirmOrder.__module__, CONTENT_SENTINEL),
    )
    exhausted = stalled(
        "FailRefusingSaga",
        _pending("ReserveItems", ReserveItems.__module__, "ORD-42"),
        retry_count=9,
    )
    unknown = SagaState(
        saga_type="RetiredSaga",
        correlation_id=uuid4(),
        status=SagaStatus.COMPENSATING,
    )
    unhydratable = stalled(
        "ReserveThenConfirmSaga", _pending("ReserveItems", _GONE, CONTENT_SENTINEL)
    )
    healthy = stalled(
        "ReserveThenConfirmSaga",
        _pending("ReserveItems", ReserveItems.__module__, "ORD-43"),
    )
    for state in (down, exhausted, unknown, unhydratable, healthy):
        await repo.save(state)

    with caplog.at_level(logging.DEBUG, logger="pydomain"):
        not_recovered = await manager.recover_pending_sagas()

    assert set(not_recovered) == {down.id, exhausted.id, unknown.id, unhydratable.id}
    assert len(not_recovered) == 4
    assert bus.sent == ["ReserveItems"]
    assert_absent_from_logs(caplog)


class ReentrantFailSaga(Saga[SagaState]):
    """A fail=True step whose forward command raises an event it listens to."""

    listens_to = [OrderCreated, PaymentDeclined, ItemsReserved]

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)
        self.on(
            OrderCreated,
            send=lambda e: ProcessPayment(order_id=e.order_id),
            compensate=lambda e: CancelPayment(order_id=e.order_id),
        )
        self.on(
            PaymentDeclined, send=lambda e: ReserveItems(order_id=e.order_id), fail=True
        )
        self.on(ItemsReserved, send=lambda e: ConfirmOrder(order_id=e.order_id))


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="SAGA-20 (W2-5): the forward command's event re-enters the saga "
    "mid-compensation and drives a forward step",
)
@pytest.mark.anyio
async def test_reentrant_event_does_not_drive_a_forward_step_mid_compensation() -> None:
    """A compensating saga should not dispatch a forward step for an event
    its own failing step's command raised (SAGA-20, W2-5)."""
    bus = MessageBus()
    reservations = FakeRepository[Reservation, Any]()
    seen: list[str] = []

    async def reserve(command: Any, uow: Any) -> EmptyCommandResult:
        seen.append("ReserveItems")
        reservation = Reservation(id=uuid4(), order_id=command.order_id)
        reservation._add_event(  # pyright: ignore[reportPrivateUsage]
            ItemsReserved(order_id=command.order_id)
        )
        await reservations.save(reservation)
        return EmptyCommandResult()

    def recording(name: str) -> Any:
        async def handler(command: Any, uow: Any) -> EmptyCommandResult:
            seen.append(name)
            return EmptyCommandResult()

        return handler

    bus.register_command(ReserveItems, reserve, lambda: FakeUnitOfWork(reservations))
    for command_type in (ProcessPayment, CancelPayment, ConfirmOrder):
        bus.register_command(
            command_type,
            recording(command_type.__name__),
            lambda: FakeUnitOfWork(reservations),
        )
    registry = SagaRegistry()
    registry.register_saga(ReentrantFailSaga)
    manager = SagaManager(FakeSagaRepository(), registry, bus)
    manager.bind_to(bus)
    cid = uuid4()

    await bus.dispatch(OrderCreated(order_id="ORD-44", correlation_id=cid))
    await bus.dispatch(PaymentDeclined(order_id="ORD-44", correlation_id=cid))

    assert "ConfirmOrder" not in seen


@pytest.mark.anyio
async def test_hydration_reason_names_the_command_that_stops_recovery_now() -> None:
    """Once the first gap is fixed, the reason names the next unhydratable
    command (B-91, B-92)."""
    manager, repo = _manager(ApprovalSaga, RecordingBus())
    state = await _saved(
        repo,
        status=SagaStatus.RUNNING,
        pending_commands=[
            _pending("ReserveItems", _GONE, "ORD-45a"),
            _pending("ConfirmOrder", "orders.other_renamed_module", "ORD-45b"),
        ],
    )
    await manager.recover_pending_sagas()
    await _fix_import_paths(repo, state)

    await manager.recover_pending_sagas()

    stored = await repo.get_by_id(state.id)
    assert stored is not None
    assert stored.status == SagaStatus.SUSPENDED
    assert stored.suspension_reason == "Could not hydrate ConfirmOrder"


@pytest.mark.anyio
async def test_failed_forward_command_record_and_log_name_the_command_and_saga(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The record keeps the command's type, module and data; the log line
    names the saga (B-83)."""
    with caplog.at_level(logging.DEBUG, logger="pydomain"):
        state, _ = await _decline_through_message_bus()

    [record] = state.failed_compensations
    assert record["command_type"] == "ProcessPayment"
    assert record["module_name"] == ProcessPayment.__module__
    assert record["data"]["order_id"] == "ORD-34"
    assert any(
        r.getMessage().startswith("Forward command ProcessPayment")
        and str(state.id) in r.getMessage()
        for r in caplog.records
    )
