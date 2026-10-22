"""DCE-146 (ADR-079 points 4 and 5, W2-1 phase 4): a command is reserved before
its handler runs, so concurrent duplicates run it once. Row CQRS-04; decisions
B-106, B-107, B-117, B-118, B-120, B-121, B-123, B-125, B-129.

Every dispatch that a test cancels runs in its own task, and the test reads
its outcome with :func:`_outcome`, so no ``CancelledError`` reaches the test's
own task. Synthetic order data only.
"""

from __future__ import annotations

import asyncio
import logging
import types
from collections.abc import Awaitable, Coroutine, Generator, Hashable
from typing import Any
from uuid import UUID, uuid4

import pytest

from pydomain.cqrs import (
    Command,
    CommandBus,
    CommandExecutionError,
    CommandResult,
)
from pydomain.cqrs.behaviors import AggregateLockingBehavior, IdempotencyBehavior
from pydomain.cqrs.exceptions import CommandInProgressError
from pydomain.cqrs.idempotency import (
    MISSING,
    MessageReservationStore,
    ProcessedMessageStore,
)
from pydomain.cqrs.locking import DictLockKeyResolver
from pydomain.testing import (
    FakeLockProvider,
    FakeProcessedMessageStore,
    FakeUnitOfWork,
)

# ---------------------------------------------------------------------------
# Synthetic domain and helpers
# ---------------------------------------------------------------------------


class _Placed(CommandResult):
    order_id: UUID


class _PlaceOrder(Command[_Placed]):
    order_id: UUID


class _Refused(Exception):
    """The handler refuses the order."""


class _CountingUoW(FakeUnitOfWork):
    """Records each completed storage commit and each rollback."""

    def __init__(self, *, commit_gate: asyncio.Event | None = None, **kwargs: Any):
        super().__init__(**kwargs)
        self.commits = 0
        self.commit_gate = commit_gate
        self.commit_entered = asyncio.Event()

    async def _commit(self) -> None:
        await super()._commit()
        self.commit_entered.set()
        if self.commit_gate is not None:
            await self.commit_gate.wait()
        self.commits += 1


class _Run:
    """One handler run's script: a gate to wait on and whether to refuse."""

    def __init__(self, *, gated: bool = False, refuse: bool = False) -> None:
        self.entered = asyncio.Event()
        self.gate = asyncio.Event()
        if not gated:
            self.gate.set()
        self.refuse = refuse


class _Harness:
    """A bus whose handler counts its runs and follows a script per run.

    Each dispatch gets a fresh :class:`_CountingUoW` (built by *uow_factory*
    when given); ``units`` keeps them in dispatch order.
    """

    def __init__(
        self,
        store: ProcessedMessageStore,
        *,
        locks: bool = False,
        runs: list[_Run] | None = None,
        uow_factory: Any = None,
    ) -> None:
        self.store = store
        self.script = list(runs or [])
        self.runs = 0
        self.units: list[_CountingUoW] = []
        self._uow_factory = uow_factory or _CountingUoW
        behaviors: list[Any] = [IdempotencyBehavior(store)]
        if locks:
            resolver = DictLockKeyResolver()
            resolver.register(_PlaceOrder, lambda m: [f"order:{m.order_id}"])
            behaviors.append(AggregateLockingBehavior(FakeLockProvider(), resolver))
        self.bus = CommandBus()
        self.bus.register(
            _PlaceOrder, self._handle, uow_factory=self._unit, behaviors=behaviors
        )

    def _unit(self) -> _CountingUoW:
        unit = self._uow_factory()
        self.units.append(unit)
        return unit

    async def _handle(self, cmd: _PlaceOrder, uow: _CountingUoW) -> _Placed:
        self.runs += 1
        run = self.script.pop(0) if self.script else _Run()
        run.entered.set()
        await asyncio.sleep(0)  # a handler awaits, as a real one does
        await run.gate.wait()
        if run.refuse:
            raise _Refused("synthetic refusal")
        return _Placed(order_id=cmd.order_id)

    def start(self, command: _PlaceOrder) -> asyncio.Task[Any]:
        return asyncio.ensure_future(self.bus.dispatch(command))

    @property
    def commits(self) -> int:
        return sum(unit.commits for unit in self.units)


_HANG_GUARD = 2.0
"""Seconds after which an awaited step that should have happened counts as hung."""


async def _outcome(awaitable: Awaitable[Any]) -> BaseException | None:
    """Await *awaitable*; return what it raised, ``None`` if it returned."""
    try:
        async with asyncio.timeout(_HANG_GUARD):
            await awaitable
    except BaseException as error:  # a cancellation of a dispatch task included
        return error
    return None


async def _reached(event: asyncio.Event) -> None:
    """Wait for *event*; fail with ``TimeoutError`` if it never comes."""
    async with asyncio.timeout(_HANG_GUARD):
        await event.wait()


async def _settle(rounds: int = 5) -> None:
    """Let the other tasks run: a cancellation is delivered at their next step."""
    for _ in range(rounds):
        await asyncio.sleep(0)


def _in_progress(error: BaseException | None) -> bool:
    """Whether *error* is the bus's wrapper of ``CommandInProgressError``."""
    return isinstance(error, CommandExecutionError) and isinstance(
        error.__cause__, CommandInProgressError
    )


async def _is_free(store: Any, message_id: UUID) -> bool:
    """Whether *message_id* can be reserved now; frees the probe's reservation."""
    token = await store.reserve(message_id)
    if token is None:
        return False
    await store.release(message_id, token)
    return True


_TTL = 0.05
"""A reservation TTL short enough to wait out in a test."""


class _ResultsOnly:
    """A ``ProcessedMessageStore`` without ``MessageReservationStore``."""

    def __init__(self) -> None:
        self.results: dict[UUID, Any] = {}

    async def get(self, message_id: UUID) -> Any:
        return self.results.get(message_id, MISSING)

    async def set(self, message_id: UUID, result: Any) -> None:
        self.results[message_id] = result

    async def contains(self, message_id: UUID) -> bool:
        return message_id in self.results

    async def check_and_set(self, message_id: UUID) -> bool:
        raise AssertionError("not used by IdempotencyBehavior")


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------


class TestConcurrentDuplicates:
    @pytest.mark.anyio
    @pytest.mark.parametrize("locks", [False, True], ids=["no-locks", "locks"])
    async def test_concurrent_duplicates_run_the_handler_once(
        self, locks: bool
    ) -> None:
        """One runs, the other gets ``CommandInProgressError`` (wrapped) and
        commits nothing. The audit probe ``documented(idem,lock)`` of
        ``cqrs/p_04_09.py`` is the ``locks`` case."""
        harness = _Harness(FakeProcessedMessageStore(), locks=locks)
        command = _PlaceOrder(order_id=uuid4())

        outcomes = await asyncio.gather(
            harness.bus.dispatch(command),
            harness.bus.dispatch(command),
            return_exceptions=True,
        )

        assert harness.runs == 1
        assert harness.commits == 1
        results = [o for o in outcomes if not isinstance(o, BaseException)]
        refused = [o for o in outcomes if isinstance(o, BaseException)]
        assert [result for result, _ in results] == [_Placed(order_id=command.order_id)]
        assert len(refused) == 1
        assert _in_progress(refused[0])
        assert sorted(unit.rolled_back for unit in harness.units) == [False, True]

    @pytest.mark.anyio
    async def test_command_in_progress_error_names_the_command_type_only(
        self,
    ) -> None:
        run = _Run(gated=True)
        harness = _Harness(FakeProcessedMessageStore(), runs=[run])
        command = _PlaceOrder(order_id=uuid4())
        first = harness.start(command)
        await _reached(run.entered)

        error = await _outcome(harness.bus.dispatch(command))
        run.gate.set()
        first_error = await _outcome(first)

        assert first_error is None
        assert _in_progress(error)
        assert error is not None
        cause = str(error.__cause__)
        assert cause == "Command _PlaceOrder is already in progress"
        assert str(command.order_id) not in cause
        assert str(command.command_id) not in cause

    @pytest.mark.anyio
    async def test_duplicate_after_completion_gets_the_cached_result(self) -> None:
        harness = _Harness(FakeProcessedMessageStore())
        command = _PlaceOrder(order_id=uuid4())

        first, _ = await harness.bus.dispatch(command)
        again, events = await harness.bus.dispatch(command)

        assert again == first == _Placed(order_id=command.order_id)
        assert events == []
        assert harness.runs == 1

    @pytest.mark.anyio
    async def test_duplicate_that_finds_the_result_after_reserve_gets_it(
        self,
    ) -> None:
        """``reserve`` returns ``None`` because the first dispatch completed
        between the duplicate's ``get`` and its ``reserve``: the second ``get``
        returns the result (ADR-079 point 4, step 2)."""

        class MissesTheFirstGet(FakeProcessedMessageStore):
            gets = 0

            async def get(self, message_id: UUID) -> Any:
                self.gets += 1
                return MISSING if self.gets == 1 else await super().get(message_id)

        store = MissesTheFirstGet()
        harness = _Harness(store)
        command = _PlaceOrder(order_id=uuid4())
        assert await store.reserve(command.command_id) is not None
        await store.set(command.command_id, _Placed(order_id=command.order_id))

        result, _ = await harness.bus.dispatch(command)

        assert result == _Placed(order_id=command.order_id)
        assert harness.runs == 0

    @pytest.mark.anyio
    async def test_store_without_reservation_keeps_get_and_set(self) -> None:
        """A ``ProcessedMessageStore``-only store keeps the old flow, and its
        known limit: two concurrent duplicates both run."""

        store = _ResultsOnly()
        assert isinstance(store, ProcessedMessageStore)
        assert not isinstance(store, MessageReservationStore)
        harness = _Harness(store)
        command = _PlaceOrder(order_id=uuid4())

        outcomes = await asyncio.gather(
            harness.bus.dispatch(command), harness.bus.dispatch(command)
        )

        assert harness.runs == 2
        assert [result for result, _ in outcomes] == [
            _Placed(order_id=command.order_id)
        ] * 2
        assert store.results == {command.command_id: _Placed(order_id=command.order_id)}

    @pytest.mark.anyio
    async def test_store_without_reservation_lets_a_failed_command_run_again(
        self,
    ) -> None:
        """No reservation to release: the handler's error reaches the caller
        unchanged, and the retry runs."""
        store = _ResultsOnly()
        harness = _Harness(store, runs=[_Run(refuse=True)])
        command = _PlaceOrder(order_id=uuid4())

        error = await _outcome(harness.bus.dispatch(command))
        result, _ = await harness.bus.dispatch(command)

        assert isinstance(error, CommandExecutionError)
        assert isinstance(error.__cause__, _Refused)
        assert getattr(error.__cause__, "__notes__", []) == []
        assert result == _Placed(order_id=command.order_id)
        assert harness.runs == 2


# ---------------------------------------------------------------------------
# Release and completion
# ---------------------------------------------------------------------------


class TestReleaseAndCompletion:
    @pytest.mark.anyio
    @pytest.mark.parametrize("fail_at", ["handler", "flush", "write_outbox", "commit"])
    async def test_failed_command_releases_its_reservation(self, fail_at: str) -> None:
        """A failed handler or commit (through ``fail_at``) releases the
        reservation: the id is free, and the retry runs."""
        store = FakeProcessedMessageStore()
        first = _Run(refuse=True) if fail_at == "handler" else _Run()
        hook = None if fail_at == "handler" else fail_at
        units = [_CountingUoW(fail_at=hook), _CountingUoW()]  # type: ignore[arg-type]
        harness = _Harness(store, runs=[first], uow_factory=lambda: units.pop(0))
        command = _PlaceOrder(order_id=uuid4())

        error = await _outcome(harness.bus.dispatch(command))
        free = await _is_free(store, command.command_id)
        result, _ = await harness.bus.dispatch(command)

        assert isinstance(error, CommandExecutionError)
        assert free
        assert result == _Placed(order_id=command.order_id)
        assert harness.runs == 2
        assert harness.commits == 1

    @pytest.mark.anyio
    async def test_command_cancelled_before_the_commit_releases_its_reservation(
        self,
    ) -> None:
        store = FakeProcessedMessageStore()
        run = _Run(gated=True)
        harness = _Harness(store, runs=[run])
        command = _PlaceOrder(order_id=uuid4())
        task = harness.start(command)
        await _reached(run.entered)

        task.cancel()
        error = await _outcome(task)
        free = await _is_free(store, command.command_id)
        result, _ = await harness.bus.dispatch(command)

        assert isinstance(error, asyncio.CancelledError)
        assert free
        assert result == _Placed(order_id=command.order_id)
        assert harness.runs == 2
        assert harness.commits == 1

    @pytest.mark.anyio
    async def test_cancelled_committed_command_completes_its_reservation(
        self,
    ) -> None:
        """Not released: the retry gets the cached result (B-121)."""
        store = FakeProcessedMessageStore()
        gate = asyncio.Event()
        committing = _CountingUoW(commit_gate=gate)
        units = [committing, _CountingUoW()]
        harness = _Harness(store, uow_factory=lambda: units.pop(0))
        command = _PlaceOrder(order_id=uuid4())
        task = harness.start(command)
        await _reached(committing.commit_entered)

        task.cancel()
        await _settle()
        gate.set()
        error = await _outcome(task)
        free = await _is_free(store, command.command_id)
        result, _ = await harness.bus.dispatch(command)

        assert isinstance(error, asyncio.CancelledError)
        assert not free
        assert result == _Placed(order_id=command.order_id)
        assert harness.runs == 1
        assert harness.commits == 1

    @pytest.mark.anyio
    async def test_release_after_a_ttl_expiry_does_not_free_a_newer_reservation(
        self,
    ) -> None:
        """A's reservation expires while its handler runs; B reserves the id;
        A then fails and releases its stale token, which frees nothing, so C is
        still refused while B runs (B-120)."""
        store = FakeProcessedMessageStore(reservation_ttl=_TTL)
        a, b = _Run(gated=True, refuse=True), _Run(gated=True)
        harness = _Harness(store, runs=[a, b])
        command = _PlaceOrder(order_id=uuid4())
        task_a = harness.start(command)
        await _reached(a.entered)
        await asyncio.sleep(_TTL * 2)
        task_b = harness.start(command)
        await _reached(b.entered)

        a.gate.set()
        error_a = await _outcome(task_a)
        error_c = await _outcome(harness.bus.dispatch(command))
        b.gate.set()
        error_b = await _outcome(task_b)

        assert isinstance(error_a, CommandExecutionError)
        assert isinstance(error_a.__cause__, _Refused)
        assert _in_progress(error_c)
        assert error_b is None
        assert harness.runs == 2

    @pytest.mark.anyio
    async def test_failed_post_commit_record_keeps_the_reservation_until_the_ttl(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The dispatch returns its result with a type-only WARNING; an
        immediate duplicate is refused; after the TTL a retry runs the handler
        again (B-129)."""

        class FailingSet(FakeProcessedMessageStore):
            async def set(self, message_id: UUID, result: Any) -> None:
                raise RuntimeError("synthetic-record-detail")

        caplog.set_level(logging.WARNING, logger="pydomain.pipeline")
        store = FailingSet(reservation_ttl=_TTL)
        harness = _Harness(store)
        command = _PlaceOrder(order_id=uuid4())

        result, _ = await harness.bus.dispatch(command)
        duplicate = await _outcome(harness.bus.dispatch(command))
        await asyncio.sleep(_TTL * 2)
        retried, _ = await harness.bus.dispatch(command)

        assert result == retried == _Placed(order_id=command.order_id)
        assert _in_progress(duplicate)
        assert harness.runs == 2
        warnings = [
            r.getMessage() for r in caplog.records if r.name == "pydomain.pipeline"
        ]
        assert warnings[0] == "Idempotency record failed for _PlaceOrder: RuntimeError"
        assert "synthetic-record-detail" not in " ".join(warnings)


# ---------------------------------------------------------------------------
# Cancellation and failure of reserve and release
# ---------------------------------------------------------------------------


class _GatedReserveStore(FakeProcessedMessageStore):
    """``reserve`` waits on a gate; ``release`` calls are recorded."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.gate = asyncio.Event()
        self.tokens: list[Hashable | None] = []
        self.released: list[Hashable] = []

    async def reserve(self, message_id: UUID) -> Hashable | None:
        self.entered.set()
        await self.gate.wait()
        token = await super().reserve(message_id)
        self.tokens.append(token)
        return token

    async def release(self, message_id: UUID, token: Hashable) -> None:
        self.released.append(token)
        await super().release(message_id, token)


class _FailingRelease(FakeProcessedMessageStore):
    """``release`` raises; the reservation stays until the TTL."""

    async def release(self, message_id: UUID, token: Hashable) -> None:
        raise RuntimeError("synthetic-release-detail")


class _FailingReserve(FakeProcessedMessageStore):
    """``reserve`` raises before it reserves anything."""

    async def reserve(self, message_id: UUID) -> Hashable | None:
        raise RuntimeError("synthetic-reserve-detail")


class _GatedReserveFailingRelease(_GatedReserveStore):
    """``reserve`` waits on a gate; ``release`` raises."""

    async def release(self, message_id: UUID, token: Hashable) -> None:
        self.released.append(token)
        raise RuntimeError("synthetic-release-detail")


class TestReserveAndReleaseFailures:
    @pytest.mark.anyio
    async def test_failing_reserve_reaches_the_caller_before_the_handler(
        self,
    ) -> None:
        harness = _Harness(_FailingReserve())
        command = _PlaceOrder(order_id=uuid4())

        error = await _outcome(harness.bus.dispatch(command))

        assert isinstance(error, CommandExecutionError)
        assert isinstance(error.__cause__, RuntimeError)
        assert getattr(error.__cause__, "__notes__", []) == []
        assert harness.runs == 0

    @pytest.mark.anyio
    async def test_failed_release_after_a_cancelled_reserve_is_a_note(self) -> None:
        """B-125 with a failing ``release``: the cancellation propagates with a
        type-only note, and the id stays reserved."""
        store = _GatedReserveFailingRelease()
        harness = _Harness(store)
        command = _PlaceOrder(order_id=uuid4())
        task = harness.start(command)
        await _reached(store.entered)

        task.cancel()
        await _settle()
        store.gate.set()
        error = await _outcome(task)

        assert isinstance(error, asyncio.CancelledError)
        assert store.released == [store.tokens[0]]
        notes = getattr(error, "__notes__", [])
        assert notes == ["clean-up also failed: RuntimeError"]
        assert "synthetic-release-detail" not in " ".join(notes)
        assert (
            await FakeProcessedMessageStore.reserve(store, command.command_id) is None
        )
        assert harness.runs == 0

    @pytest.mark.anyio
    async def test_cancel_during_reserve_releases_the_new_token(self) -> None:
        """``reserve`` completes under the shield; its token is released at
        once, the cancellation propagates, and a retry can reserve (B-125)."""
        store = _GatedReserveStore()
        harness = _Harness(store)
        command = _PlaceOrder(order_id=uuid4())
        task = harness.start(command)
        await _reached(store.entered)

        task.cancel()
        await _settle()
        store.gate.set()
        error = await _outcome(task)
        result, _ = await harness.bus.dispatch(command)

        assert isinstance(error, asyncio.CancelledError)
        assert store.tokens[0] is not None
        assert store.released == [store.tokens[0]]
        assert result == _Placed(order_id=command.order_id)
        assert harness.runs == 1

    @pytest.mark.anyio
    async def test_failed_release_is_a_note_on_the_error(self) -> None:
        """A failing ``release`` is a type-only note on the handler's error, and
        the id stays blocked until the TTL (ADR-079, Negative)."""
        store = _FailingRelease(reservation_ttl=_TTL)
        harness = _Harness(store, runs=[_Run(refuse=True)])
        command = _PlaceOrder(order_id=uuid4())

        error = await _outcome(harness.bus.dispatch(command))
        blocked = await _outcome(harness.bus.dispatch(command))
        await asyncio.sleep(_TTL * 2)
        result, _ = await harness.bus.dispatch(command)

        assert isinstance(error, CommandExecutionError)
        assert isinstance(error.__cause__, _Refused)
        notes = getattr(error.__cause__, "__notes__", [])
        assert notes == ["clean-up also failed: RuntimeError"]
        assert "synthetic-release-detail" not in " ".join(notes)
        assert _in_progress(blocked)
        assert result == _Placed(order_id=command.order_id)
        assert harness.runs == 2


# ---------------------------------------------------------------------------
# A suspended dispatch closed (GeneratorExit)
# ---------------------------------------------------------------------------


@types.coroutine
def _park() -> Generator[str, None, None]:
    """Suspend the coroutine that awaits this, yielding a marker to its driver."""
    yield "parked"


async def _drive_until_parked(coroutine: Coroutine[Any, Any, Any]) -> None:
    """Drive *coroutine* by hand until it parks, awaiting what it waits on."""
    while True:
        waited = coroutine.send(None)
        if waited == "parked":
            return
        waited._asyncio_future_blocking = False  # taken over from the driver
        await waited


@pytest.mark.anyio
async def test_closing_a_suspended_dispatch_keeps_its_reservation() -> None:
    """A dispatch closed while parked in its handler gets ``GeneratorExit``
    and awaits nothing, so its reservation is not released: it stays until
    the TTL frees it."""
    store = FakeProcessedMessageStore()

    async def parks(cmd: _PlaceOrder, uow: FakeUnitOfWork) -> _Placed:
        await _park()
        return _Placed(order_id=cmd.order_id)

    bus = CommandBus()
    bus.register(
        _PlaceOrder,
        parks,
        uow_factory=FakeUnitOfWork,
        behaviors=[IdempotencyBehavior(store)],
    )
    command = _PlaceOrder(order_id=uuid4())
    dispatching = bus.dispatch(command)
    await _drive_until_parked(dispatching)

    dispatching.close()

    assert not await _is_free(store, command.command_id)
    assert await store.get(command.command_id) is MISSING


@pytest.mark.anyio
async def test_closing_a_dispatch_suspended_in_reserve_awaits_nothing() -> None:
    """A dispatch closed after ``reserve`` returned a token, but before it
    resumed, re-raises ``GeneratorExit`` without awaiting a release (a closing
    coroutine cannot await); the reservation stays until the TTL frees it."""
    store = _GatedReserveStore()
    harness = _Harness(store)
    command = _PlaceOrder(order_id=uuid4())
    dispatching = harness.bus.dispatch(command)
    dispatching.send(None)  # suspended on the shielded reserve
    await _reached(store.entered)
    store.gate.set()
    await _settle()  # reserve returned its token; the dispatch has not resumed

    dispatching.close()

    assert len(store.tokens) == 1
    assert store.tokens[0] is not None
    assert store.released == []
    assert not await _is_free(store, command.command_id)
    assert harness.runs == 0
