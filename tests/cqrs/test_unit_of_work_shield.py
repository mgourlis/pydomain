"""DCE-145 (ADR-079 point 6, W2-1 phase 3): ``_commit()`` and ``_rollback()``
are shielded from cancellation, and a committed command is recorded. Row
CQRS-07; decisions B-108 to B-110, B-115, B-118, B-119, B-121 to B-124, B-126,
B-130 to B-132, B-134.

Every dispatch that a test cancels runs in its own task, and the test reads
its outcome with :func:`_outcome`, so no ``CancelledError`` reaches the test's
own task.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from collections.abc import Awaitable
from typing import Any, ClassVar
from uuid import UUID, uuid4

import anyio
import pytest

from pydomain.cqrs import (
    Command,
    CommandBus,
    CommandExecutionError,
    CommandResult,
    CQRSError,
    UnitOfWorkAlreadyCommittedError,
)
from pydomain.cqrs.behaviors import (
    AggregateLockingBehavior,
    IdempotencyBehavior,
    MessageContext,
    NextHandler,
)
from pydomain.cqrs.idempotency import MISSING
from pydomain.cqrs.locking import DictLockKeyResolver
from pydomain.cqrs.unit_of_work import AbstractUnitOfWork
from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.testing import (
    FakeProcessedMessageStore,
    FakeRepository,
    FakeUnitOfWork,
)

# ---------------------------------------------------------------------------
# Synthetic domain: placing an order writes one row and stages one event.
# ---------------------------------------------------------------------------


class _OrderPlaced(DomainEvent):
    order_id: UUID


class _Order(AggregateRoot[UUID]):
    @classmethod
    def place(cls, order_id: UUID) -> _Order:
        order = cls(id=order_id)
        order._add_event(_OrderPlaced(order_id=order_id))
        return order


class _Placed(CommandResult):
    order_id: UUID


class _PlaceOrder(Command[_Placed]):
    order_id: UUID
    refuse: bool = False


class _Refused(Exception):
    """The handler refuses the order after it staged its work."""


class _StorageFailed(Exception):
    """A storage hook failed."""


class _OrderUoW(FakeUnitOfWork):
    """Storage that writes on commit; one hook can wait on a gate.

    ``db`` holds the committed rows and ``pending`` this scope's writes.
    ``_commit`` yields to the loop before it stores them, as a database round
    trip does, so a cancellation that is not shielded interrupts it. The hook
    named by *gate_at* sets ``entered`` and waits for ``gate``. ``hooks`` logs
    each hook as it starts and ``"committed"`` or ``"rolled back"`` when the
    storage work finished; ``tasks`` records the task each hook ran in.
    """

    def __init__(
        self,
        *,
        gate_at: str | None = None,
        commit_failure: BaseException | None = None,
    ) -> None:
        self.orders: FakeRepository[_Order, UUID] = FakeRepository()
        super().__init__(repositories={"orders": self.orders})
        self.db: dict[UUID, str] = {}
        self.pending: dict[UUID, str] = {}
        self.hooks: list[str] = []
        self.tasks: dict[str, asyncio.Task[Any] | None] = {}
        self.gate_at = gate_at
        self.commit_failure = commit_failure
        self.entered = asyncio.Event()
        self.gate = asyncio.Event()

    async def _wait(self, hook: str) -> None:
        self.hooks.append(hook)
        self.tasks[hook] = asyncio.current_task()
        if hook == self.gate_at:
            self.entered.set()
            await self.gate.wait()

    async def _flush(self) -> None:
        await self._wait("flush")

    async def _write_outbox(self) -> None:
        await self._wait("write_outbox")

    async def _commit(self) -> None:
        await self._wait("commit")
        await asyncio.sleep(0)
        if self.commit_failure is not None:
            raise self.commit_failure
        self.db.update(self.pending)
        self.hooks.append("committed")

    async def _rollback(self) -> None:
        await self._wait("rollback")
        await asyncio.sleep(0)
        self.pending.clear()
        self.hooks.append("rolled back")


async def _place(cmd: _PlaceOrder, uow: _OrderUoW) -> _Placed:
    await uow.orders.save(_Order.place(cmd.order_id))
    uow.pending[cmd.order_id] = "placed"
    if cmd.refuse:
        raise _Refused("synthetic refusal")
    return _Placed(order_id=cmd.order_id)


def _bus(uow: AbstractUnitOfWork, behaviors: list[Any] | None = None) -> CommandBus:
    bus = CommandBus()
    bus.register(_PlaceOrder, _place, uow_factory=lambda: uow, behaviors=behaviors)
    return bus


async def _outcome(awaitable: Awaitable[Any]) -> BaseException | None:
    """Await *awaitable*; return what it raised, ``None`` if it returned."""
    try:
        await awaitable
    except BaseException as error:  # a cancellation of a dispatch task included
        return error
    return None


async def _settle(rounds: int = 5) -> None:
    """Let the other tasks run: a cancellation is delivered at their next step."""
    for _ in range(rounds):
        await asyncio.sleep(0)


def _start(bus: CommandBus, command: _PlaceOrder) -> asyncio.Task[Any]:
    return asyncio.ensure_future(bus.dispatch(command))


# ---------------------------------------------------------------------------
# Native asyncio cancellation
# ---------------------------------------------------------------------------


class TestNativeCancellation:
    @pytest.mark.anyio
    @pytest.mark.parametrize("hook", ["flush", "write_outbox"])
    async def test_cancel_during_flush_or_outbox_rolls_back(self, hook: str) -> None:
        """B-110: they run before the point of no return."""
        uow = _OrderUoW(gate_at=hook)
        task = _start(_bus(uow), _PlaceOrder(order_id=uuid4()))
        await uow.entered.wait()

        task.cancel()
        error = await _outcome(task)

        assert isinstance(error, asyncio.CancelledError)
        assert uow.db == {}
        assert uow.hooks.count("rollback") == 1
        assert "commit" not in uow.hooks
        assert uow.collect_events() == []

    @pytest.mark.anyio
    async def test_cancel_pending_before_commit_still_commits_and_propagates(
        self,
    ) -> None:
        """A cancellation requested in ``_write_outbox``, after its last await,
        is delivered at the next await, inside the commit: it completes."""

        class CancelsItselfInTheOutbox(_OrderUoW):
            async def _write_outbox(self) -> None:
                await super()._write_outbox()
                task = asyncio.current_task()
                assert task is not None
                task.cancel()

        uow = CancelsItselfInTheOutbox()
        command = _PlaceOrder(order_id=uuid4())

        error = await _outcome(_start(_bus(uow), command))

        assert isinstance(error, asyncio.CancelledError)
        assert uow.db == {command.order_id: "placed"}
        assert "rollback" not in uow.hooks

    @pytest.mark.anyio
    async def test_cancel_during_commit_completes_it_and_does_not_roll_back(
        self,
    ) -> None:
        uow = _OrderUoW(gate_at="commit")
        command = _PlaceOrder(order_id=uuid4())
        task = _start(_bus(uow), command)
        await uow.entered.wait()

        task.cancel()
        await _settle()
        uow.gate.set()
        error = await _outcome(task)

        assert isinstance(error, asyncio.CancelledError)
        assert uow.db == {command.order_id: "placed"}
        assert "rollback" not in uow.hooks
        assert [e.order_id for e in uow.collect_events()] == [command.order_id]
        assert task.cancelling() == 1

    @pytest.mark.anyio
    async def test_repeated_cancellation_during_commit_is_absorbed_until_it_ends(
        self,
    ) -> None:
        uow = _OrderUoW(gate_at="commit")
        command = _PlaceOrder(order_id=uuid4())
        task = _start(_bus(uow), command)
        await uow.entered.wait()

        for _ in range(3):
            task.cancel()
            await _settle()
        assert not task.done()
        uow.gate.set()
        error = await _outcome(task)

        assert isinstance(error, asyncio.CancelledError)
        assert uow.db == {command.order_id: "placed"}
        assert "rollback" not in uow.hooks
        assert task.cancelling() == 3

    @pytest.mark.anyio
    async def test_timeout_during_commit_keeps_the_commit(self) -> None:
        uow = _OrderUoW(gate_at="commit")
        command = _PlaceOrder(order_id=uuid4())
        bus = _bus(uow)
        timeouts: list[asyncio.Timeout] = []

        async def dispatch_with_a_timeout() -> None:
            async with asyncio.timeout(None) as timeout:
                timeouts.append(timeout)
                await bus.dispatch(command)

        task = asyncio.ensure_future(dispatch_with_a_timeout())
        await uow.entered.wait()

        timeouts[0].reschedule(asyncio.get_running_loop().time())
        await _settle()
        uow.gate.set()
        error = await _outcome(task)

        assert isinstance(error, TimeoutError)
        assert task.cancelling() == 0
        assert uow.db == {command.order_id: "placed"}
        assert "rollback" not in uow.hooks

    @pytest.mark.anyio
    async def test_cancel_during_commit_inside_a_task_group_keeps_the_commit(
        self,
    ) -> None:
        uow = _OrderUoW(gate_at="commit")
        command = _PlaceOrder(order_id=uuid4())
        bus = _bus(uow)

        async def sibling_fails() -> None:
            await uow.entered.wait()
            uow.gate.set()
            raise _StorageFailed("synthetic sibling failure")

        async def run_group() -> None:
            async with asyncio.TaskGroup() as group:
                group.create_task(bus.dispatch(command))
                group.create_task(sibling_fails())

        error = await _outcome(run_group())

        assert isinstance(error, ExceptionGroup)
        assert [type(e) for e in error.exceptions] == [_StorageFailed]
        assert uow.db == {command.order_id: "placed"}
        assert "rollback" not in uow.hooks

    @pytest.mark.anyio
    async def test_cancel_during_rollback_completes_the_rollback(self) -> None:
        """The first cancellation stops the handler; the second arrives while
        ``_rollback`` runs. It finishes, the buffers are drained, and the
        cancellation propagates."""
        uow = _OrderUoW(gate_at="rollback")
        handler_waits = asyncio.Event()

        async def slow_place(cmd: _PlaceOrder, uow: _OrderUoW) -> _Placed:
            await uow.orders.save(_Order.place(cmd.order_id))
            await handler_waits.wait()
            return _Placed(order_id=cmd.order_id)

        bus = CommandBus()
        bus.register(_PlaceOrder, slow_place, uow_factory=lambda: uow)
        task = _start(bus, _PlaceOrder(order_id=uuid4()))
        await _settle()

        task.cancel()
        await uow.entered.wait()
        task.cancel()
        await _settle()
        uow.gate.set()
        error = await _outcome(task)

        assert isinstance(error, asyncio.CancelledError)
        assert uow.hooks[-2:] == ["rollback", "rolled back"]
        assert uow.orders.pull_events() == []
        assert uow.collect_events() == []

    @pytest.mark.anyio
    async def test_commit_hook_failure_during_cancellation_is_a_note(self) -> None:
        uow = _OrderUoW(
            gate_at="commit",
            commit_failure=_StorageFailed("synthetic-commit-detail"),
        )
        task = _start(_bus(uow), _PlaceOrder(order_id=uuid4()))
        await uow.entered.wait()

        task.cancel()
        await _settle()
        uow.gate.set()
        error = await _outcome(task)

        assert isinstance(error, asyncio.CancelledError)
        notes = getattr(error, "__notes__", [])
        assert notes == ["clean-up also failed: _StorageFailed"]
        assert "synthetic-commit-detail" not in " ".join(notes)
        assert uow.hooks.count("rollback") == 1

    @pytest.mark.anyio
    async def test_hook_raising_its_own_cancelled_error_is_a_failure(self) -> None:
        """B-126, B-130: with nothing cancelling the caller, a ``CancelledError``
        from ``_commit`` is that hook's failure."""
        uow = _OrderUoW(commit_failure=asyncio.CancelledError())
        task = _start(_bus(uow), _PlaceOrder(order_id=uuid4()))

        error = await _outcome(task)

        assert isinstance(error, CommandExecutionError)
        cause = error.__cause__
        assert isinstance(cause, CQRSError)
        assert isinstance(cause.__cause__, asyncio.CancelledError)
        assert task.cancelling() == 0
        assert uow.hooks.count("rollback") == 1
        assert uow.collect_events() == []


# ---------------------------------------------------------------------------
# anyio cancel scopes
# ---------------------------------------------------------------------------


class _NextOutcome:
    """Records whether ``next()`` returned or raised, as a behavior sees it."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        try:
            result = await next()
        except BaseException as error:
            self.seen.append(f"raised {type(error).__name__}")
            raise
        self.seen.append("returned")
        return result


class TestAnyioCancelScope:
    @pytest.mark.anyio
    async def test_anyio_scope_cancel_during_commit_keeps_the_commit(self) -> None:
        """The commit completes, and the helper does not re-raise: ``next()``
        returns; anyio redelivers the cancellation at its next checkpoint."""
        scopes: list[anyio.CancelScope] = []

        class CancelsTheScope(_OrderUoW):
            async def _commit(self) -> None:
                scopes[0].cancel()
                await super()._commit()

        uow = CancelsTheScope()
        command = _PlaceOrder(order_id=uuid4())
        store = FakeProcessedMessageStore()
        observed = _NextOutcome()
        bus = _bus(uow, behaviors=[IdempotencyBehavior(store), observed])

        with anyio.CancelScope() as scope:
            scopes.append(scope)
            await bus.dispatch(command)

        assert uow.db == {command.order_id: "placed"}
        assert "rollback" not in uow.hooks
        assert observed.seen == ["returned"]
        assert await store.get(command.command_id) == _Placed(order_id=command.order_id)

    @pytest.mark.anyio
    async def test_anyio_scope_cancel_during_commit_does_not_spin(self) -> None:
        """B-122: the shield absorbs the scope's cancellation inline, without a
        busy loop: the CPU time stays far below the commit's duration."""

        class SlowCommit(_OrderUoW):
            async def _commit(self) -> None:
                await asyncio.sleep(0.3)
                await super()._commit()

        uow = SlowCommit()
        command = _PlaceOrder(order_id=uuid4())
        bus = _bus(uow)

        started = time.process_time()
        with anyio.move_on_after(0.02):
            await bus.dispatch(command)
        cpu = time.process_time() - started

        assert uow.db == {command.order_id: "placed"}
        assert cpu < 0.15


# ---------------------------------------------------------------------------
# The hook task: its own task, the caller's context, never eager
# ---------------------------------------------------------------------------

_CALLER_VALUE: contextvars.ContextVar[str] = contextvars.ContextVar("_CALLER_VALUE")
_HOOK_VALUE: contextvars.ContextVar[str] = contextvars.ContextVar("_HOOK_VALUE")


class _ContextUoW(_OrderUoW):
    """Reads the caller's context variable in ``_commit`` and sets its own."""

    def __init__(self) -> None:
        super().__init__()
        self.seen_in_commit: str | None = None

    async def _commit(self) -> None:
        self.seen_in_commit = _CALLER_VALUE.get(None)
        _HOOK_VALUE.set("set-in-commit")
        await super()._commit()


async def _dispatch_in_a_context(uow: _ContextUoW) -> str | None:
    """Set the caller's variable, dispatch, and return the hook's variable."""
    _CALLER_VALUE.set("set-by-caller")
    await _bus(uow).dispatch(_PlaceOrder(order_id=uuid4()))
    return _HOOK_VALUE.get(None)


class TestHookTask:
    @pytest.mark.anyio
    async def test_hook_task_is_built_without_eager_start(self) -> None:
        """Under ``asyncio.eager_task_factory`` a hook task that starts eagerly
        fails with "cannot enter context"; the dispatch completes instead."""
        loop = asyncio.get_running_loop()
        previous = loop.get_task_factory()
        loop.set_task_factory(asyncio.eager_task_factory)
        try:
            uow = _ContextUoW()
            hook_value = await asyncio.create_task(_dispatch_in_a_context(uow))
        finally:
            loop.set_task_factory(previous)

        assert "committed" in uow.hooks
        assert uow.seen_in_commit == "set-by-caller"
        assert hook_value == "set-in-commit"

    @pytest.mark.anyio
    async def test_hooks_share_the_callers_context(self) -> None:
        """B-119: a variable set before the dispatch is visible in ``_commit``,
        and one set in ``_commit`` is visible to the caller afterwards."""
        uow = _ContextUoW()

        hook_value = await asyncio.create_task(_dispatch_in_a_context(uow))

        assert uow.seen_in_commit == "set-by-caller"
        assert hook_value == "set-in-commit"

    @pytest.mark.anyio
    async def test_shielded_hooks_run_in_their_own_task_by_default(self) -> None:
        committing = _OrderUoW()
        await _start(_bus(committing), _PlaceOrder(order_id=uuid4()))
        refusing = _OrderUoW()
        refused = _start(_bus(refusing), _PlaceOrder(order_id=uuid4(), refuse=True))
        await _outcome(refused)

        assert committing.tasks["commit"] is not None
        assert committing.tasks["commit"] is not committing.tasks["flush"]
        assert refusing.tasks["rollback"] is not None
        assert refusing.tasks["rollback"] is not refused

    @pytest.mark.anyio
    async def test_shielded_hook_failure_is_not_logged_by_the_loop(self) -> None:
        """ADR-071: with ``asyncio.shield`` Python 3.14 logs "exception in
        shielded future" with the hook's message; the waiter logs nothing."""
        loop = asyncio.get_running_loop()
        logged: list[dict[str, Any]] = []
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: logged.append(context))
        try:
            uow = _OrderUoW(
                gate_at="commit",
                commit_failure=_StorageFailed("synthetic-commit-detail"),
            )
            task = _start(_bus(uow), _PlaceOrder(order_id=uuid4()))
            await uow.entered.wait()
            task.cancel()
            await _settle()
            uow.gate.set()
            await _outcome(task)
            await _settle()
        finally:
            loop.set_exception_handler(previous)

        assert logged == []


class _PlainUoW(FakeUnitOfWork):
    """Hooks that never yield, so a dispatch can be driven by hand."""

    def __init__(self) -> None:
        self.orders: FakeRepository[_Order, UUID] = FakeRepository()
        super().__init__(repositories={"orders": self.orders})
        self.pending: dict[UUID, str] = {}
        self.hooks: list[str] = []

    async def _commit(self) -> None:
        self.hooks.append("commit")

    async def _rollback(self) -> None:
        self.hooks.append("rollback")


def _drive_by_hand() -> tuple[_PlainUoW, _PlainUoW]:
    """Drive a committing and a refused dispatch with ``send(None)``."""
    committing = _PlainUoW()
    dispatching = _bus(committing).dispatch(_PlaceOrder(order_id=uuid4()))
    with pytest.raises(StopIteration):
        dispatching.send(None)

    refusing = _PlainUoW()
    refused = _bus(refusing).dispatch(_PlaceOrder(order_id=uuid4(), refuse=True))
    with pytest.raises(CommandExecutionError):
        refused.send(None)
    return committing, refusing


def test_hooks_run_inline_without_an_async_library() -> None:
    """B-131: a dispatch driven by hand, with no event loop, commits; the
    hooks run inline, with no anyio scope and no task."""
    committing, refusing = _drive_by_hand()

    assert committing.hooks == ["commit"]
    assert refusing.hooks == ["rollback"]


@pytest.mark.anyio
async def test_hooks_run_inline_when_driven_by_hand_in_a_loop_callback() -> None:
    """A dispatch driven by hand from a callback of a running loop has no
    current task: the hooks run inline there too."""
    loop = asyncio.get_running_loop()
    driven: asyncio.Future[tuple[_PlainUoW, _PlainUoW]] = loop.create_future()
    loop.call_soon(lambda: driven.set_result(_drive_by_hand()))

    committing, refusing = await driven

    assert committing.hooks == ["commit"]
    assert refusing.hooks == ["rollback"]


# ---------------------------------------------------------------------------
# shield_hooks = False: inline, in the calling task, cancellable (B-115)
# ---------------------------------------------------------------------------


class _InlineUoW(_OrderUoW):
    shield_hooks: ClassVar[bool] = False


class _TaskKeyedStorageUoW(FakeUnitOfWork):
    """Storage keyed by the current task, like ``async_scoped_session``
    with ``scopefunc=current_task``: each task gets its own session."""

    def __init__(self) -> None:
        super().__init__()
        self.sessions: dict[asyncio.Task[Any] | None, list[str]] = {}
        self.committed_rows: list[str] = []

    def session(self) -> list[str]:
        return self.sessions.setdefault(asyncio.current_task(), [])

    async def _commit(self) -> None:
        self.committed_rows.extend(self.session())


class _InlineTaskKeyedStorageUoW(_TaskKeyedStorageUoW):
    shield_hooks: ClassVar[bool] = False


async def _write_a_row(cmd: _PlaceOrder, uow: _TaskKeyedStorageUoW) -> _Placed:
    uow.session().append("order-row")
    return _Placed(order_id=cmd.order_id)


class TestShieldHooksOptOut:
    def test_shield_hooks_defaults_to_true(self) -> None:
        assert AbstractUnitOfWork.shield_hooks is True

    @pytest.mark.anyio
    async def test_shield_hooks_false_runs_the_hooks_in_the_calling_task(
        self,
    ) -> None:
        committing = _InlineUoW()
        committed = _start(_bus(committing), _PlaceOrder(order_id=uuid4()))
        await committed
        refusing = _InlineUoW()
        refused = _start(_bus(refusing), _PlaceOrder(order_id=uuid4(), refuse=True))
        await _outcome(refused)

        assert committing.tasks["commit"] is committed
        assert refusing.tasks["rollback"] is refused

    @pytest.mark.anyio
    async def test_shield_hooks_false_leaves_the_hooks_cancellable(self) -> None:
        uow = _InlineUoW(gate_at="commit")
        task = _start(_bus(uow), _PlaceOrder(order_id=uuid4()))
        await uow.entered.wait()

        task.cancel()
        error = await _outcome(task)

        assert isinstance(error, asyncio.CancelledError)
        assert uow.db == {}
        assert "committed" not in uow.hooks
        assert uow.hooks[-2:] == ["rollback", "rolled back"]

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("uow_type", "committed_rows"),
        [
            pytest.param(
                _TaskKeyedStorageUoW,
                [],
                id="shielded",
            ),
            pytest.param(_InlineTaskKeyedStorageUoW, ["order-row"], id="inline"),
        ],
    )
    async def test_task_keyed_storage_needs_shield_hooks_false(
        self, uow_type: type[_TaskKeyedStorageUoW], committed_rows: list[str]
    ) -> None:
        """B-119: a shielded hook runs in another task, so storage keyed by the
        current task commits another session, silently: nothing."""
        uow = uow_type()
        bus = CommandBus()
        bus.register(_PlaceOrder, _write_a_row, uow_factory=lambda: uow)

        await bus.dispatch(_PlaceOrder(order_id=uuid4()))

        assert uow.committed_rows == committed_rows


# ---------------------------------------------------------------------------
# A direct user of the unit of work
# ---------------------------------------------------------------------------


class TestDirectUse:
    @pytest.mark.anyio
    async def test_cancelled_direct_commit_is_committed(self) -> None:
        uow = _OrderUoW(gate_at="commit")
        order_id = uuid4()

        async def place_directly() -> None:
            async with uow:
                await uow.orders.save(_Order.place(order_id))
                uow.pending[order_id] = "placed"
                await uow.commit()

        task = asyncio.ensure_future(place_directly())
        await uow.entered.wait()
        task.cancel()
        await _settle()
        uow.gate.set()
        error = await _outcome(task)

        assert isinstance(error, asyncio.CancelledError)
        assert uow.db == {order_id: "placed"}
        assert "rollback" not in uow.hooks
        assert [e.order_id for e in uow.collect_events()] == [order_id]


# ---------------------------------------------------------------------------
# A committed command is recorded, whatever happens after the commit
# (B-109, B-121, B-123, B-124, B-132, B-134)
# ---------------------------------------------------------------------------


class _GatedSetStore(FakeProcessedMessageStore):
    """``set`` waits on a gate; with *failure* it then raises instead."""

    def __init__(self, failure: BaseException | None = None) -> None:
        super().__init__()
        self.failure = failure
        self.entered = asyncio.Event()
        self.gate = asyncio.Event()

    async def set(self, message_id: UUID, result: Any) -> None:
        self.entered.set()
        await self.gate.wait()
        if self.failure is not None:
            raise self.failure
        await super().set(message_id, result)


class _GatedReleaseLocks:
    """A lock provider whose ``release`` waits on a gate; no ownership."""

    def __init__(self) -> None:
        self.released: list[str] = []
        self.entered = asyncio.Event()
        self.gate = asyncio.Event()

    async def acquire(self, key: str) -> None:
        return None

    async def release(self, key: str) -> None:
        self.entered.set()
        await self.gate.wait()
        self.released.append(key)


def _counting_bus(
    units: list[_OrderUoW], behaviors: list[Any]
) -> tuple[CommandBus, list[int]]:
    """A bus whose handler counts its runs; each dispatch takes the next unit."""
    runs = [0]

    async def counted_place(cmd: _PlaceOrder, uow: _OrderUoW) -> _Placed:
        runs[0] += 1
        return await _place(cmd, uow)

    bus = CommandBus()
    bus.register(
        _PlaceOrder,
        counted_place,
        uow_factory=lambda: units.pop(0),
        behaviors=behaviors,
    )
    return bus, runs


class TestCommittedCommandIsRecorded:
    @pytest.mark.anyio
    async def test_cancelled_committed_command_is_recorded(self) -> None:
        """B-109: the retry gets the cached result; the handler runs once."""
        first = _OrderUoW(gate_at="commit")
        store = FakeProcessedMessageStore()
        bus, runs = _counting_bus([first, _OrderUoW()], [IdempotencyBehavior(store)])
        command = _PlaceOrder(order_id=uuid4())
        task = _start(bus, command)
        await first.entered.wait()

        task.cancel()
        await _settle()
        first.gate.set()
        error = await _outcome(task)
        result, events = await bus.dispatch(command)

        assert isinstance(error, asyncio.CancelledError)
        assert first.db == {command.order_id: "placed"}
        assert "rollback" not in first.hooks
        assert result == _Placed(order_id=command.order_id)
        assert events == []
        assert runs == [1]

    @pytest.mark.anyio
    async def test_cancel_pending_before_commit_is_recorded(self) -> None:
        """B-132: the commit completes, the cancellation propagates, and the
        result is recorded (B-121)."""

        class CancelsItselfInTheOutbox(_OrderUoW):
            async def _write_outbox(self) -> None:
                await super()._write_outbox()
                task = asyncio.current_task()
                assert task is not None
                task.cancel()

        store = FakeProcessedMessageStore()
        uow = CancelsItselfInTheOutbox()
        command = _PlaceOrder(order_id=uuid4())

        error = await _outcome(_start(_bus(uow, [IdempotencyBehavior(store)]), command))

        assert isinstance(error, asyncio.CancelledError)
        assert uow.db == {command.order_id: "placed"}
        assert await store.get(command.command_id) == _Placed(order_id=command.order_id)

    @pytest.mark.anyio
    async def test_cancel_during_the_post_commit_record_still_records(self) -> None:
        """B-123: the ``set`` after the commit is protected too."""
        store = _GatedSetStore()
        uow = _OrderUoW()
        command = _PlaceOrder(order_id=uuid4())
        task = _start(_bus(uow, [IdempotencyBehavior(store)]), command)
        await store.entered.wait()

        task.cancel()
        await _settle()
        store.gate.set()
        error = await _outcome(task)

        assert isinstance(error, asyncio.CancelledError)
        assert await store.get(command.command_id) == _Placed(order_id=command.order_id)

    @pytest.mark.anyio
    async def test_cancel_during_the_lock_release_after_the_commit_is_recorded(
        self,
    ) -> None:
        """B-121: the release after the commit re-raises the cancellation; the
        committed command is recorded before it propagates."""
        locks = _GatedReleaseLocks()
        resolver = DictLockKeyResolver()
        resolver.register(_PlaceOrder, lambda m: [f"order:{m.order_id}"])
        store = FakeProcessedMessageStore()
        uow = _OrderUoW()
        command = _PlaceOrder(order_id=uuid4())
        behaviors = [
            IdempotencyBehavior(store),
            AggregateLockingBehavior(locks, resolver),
        ]
        task = _start(_bus(uow, behaviors), command)
        await locks.entered.wait()

        task.cancel()
        error = await _outcome(task)
        locks.gate.set()
        await _settle()

        assert isinstance(error, asyncio.CancelledError)
        assert uow.db == {command.order_id: "placed"}
        assert locks.released == [f"order:{command.order_id}"]
        assert await store.get(command.command_id) == _Placed(order_id=command.order_id)

    @pytest.mark.anyio
    async def test_failed_record_after_a_committed_then_raised_command_is_a_note(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """B-134: the commit completed, ``next()`` raised a cancellation, and the
        ``set`` raises: a type-only note on the re-raised error, no WARNING."""
        caplog.set_level(logging.WARNING, logger="pydomain.pipeline")
        store = _GatedSetStore(failure=RuntimeError("synthetic-record-detail"))
        store.gate.set()
        uow = _OrderUoW(gate_at="commit")
        command = _PlaceOrder(order_id=uuid4())
        task = _start(_bus(uow, [IdempotencyBehavior(store)]), command)
        await uow.entered.wait()

        task.cancel()
        await _settle()
        uow.gate.set()
        error = await _outcome(task)

        assert isinstance(error, asyncio.CancelledError)
        assert store.entered.is_set()
        notes = getattr(error, "__notes__", [])
        assert notes == ["clean-up also failed: RuntimeError"]
        assert "synthetic-record-detail" not in " ".join(notes)
        assert [r for r in caplog.records if r.name == "pydomain.pipeline"] == []
        assert await store.get(command.command_id) is MISSING

    @pytest.mark.anyio
    async def test_committed_then_cancelled_command_does_not_reach_in_process_handlers(
        self,
    ) -> None:
        """B-124: the commit completed and the dispatch raised, so no events are
        returned and no in-process handler sees them; only the outbox path
        delivers them. A retry returns the cached result without them."""
        uow = _OrderUoW(gate_at="commit")
        store = FakeProcessedMessageStore()
        handled: list[DomainEvent] = []

        async def on_placed(event: _OrderPlaced) -> None:
            handled.append(event)

        units = [uow, _OrderUoW()]
        bus = MessageBus()
        bus.register_command(
            _PlaceOrder,
            _place,
            uow_factory=lambda: units.pop(0),
            behaviors=[IdempotencyBehavior(store)],
        )
        bus.register_event(_OrderPlaced, on_placed)
        command = _PlaceOrder(order_id=uuid4())
        task = asyncio.ensure_future(bus.dispatch(command))
        await uow.entered.wait()

        task.cancel()
        await _settle()
        uow.gate.set()
        error = await _outcome(task)
        retried = await bus.dispatch(command)

        assert isinstance(error, asyncio.CancelledError)
        assert uow.db == {command.order_id: "placed"}
        assert retried == _Placed(order_id=command.order_id)
        assert handled == []

    @pytest.mark.anyio
    async def test_handler_that_commits_itself_is_recorded(self) -> None:
        """The handler's own commit (against ADR-020) persisted, so the scope is
        committed when the terminal step's ``commit()`` raises
        ``UnitOfWorkAlreadyCommittedError``: the command is recorded (B-121)
        and its retry does not run the handler again."""
        runs = 0
        units = [_OrderUoW(), _OrderUoW()]

        async def commits_itself(cmd: _PlaceOrder, uow: _OrderUoW) -> _Placed:
            nonlocal runs
            runs += 1
            result = await _place(cmd, uow)
            await uow.commit()
            return result

        store = FakeProcessedMessageStore()
        bus = CommandBus()
        bus.register(
            _PlaceOrder,
            commits_itself,
            uow_factory=lambda: units.pop(0),
            behaviors=[IdempotencyBehavior(store)],
        )
        command = _PlaceOrder(order_id=uuid4())

        with pytest.raises(CommandExecutionError) as raised:
            await bus.dispatch(command)
        result, _ = await bus.dispatch(command)

        assert isinstance(raised.value.__cause__, UnitOfWorkAlreadyCommittedError)
        assert result == _Placed(order_id=command.order_id)
        assert runs == 1
