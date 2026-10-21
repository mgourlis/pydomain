"""Reviewer probes for PR #112 (DCE-123). Self-contained; synthetic data only."""

from __future__ import annotations

import asyncio
import gc
import logging
from typing import Any

import anyio
import pytest

from pydomain.cqrs import Command, CommandBus, CommandResult
from pydomain.cqrs import behaviors as B
from pydomain.cqrs.behaviors import AggregateLockingBehavior, MessageContext
from pydomain.cqrs.exceptions import LockReentryError
from pydomain.cqrs.locking import DictLockKeyResolver, LockProvider
from pydomain.testing import FakeLockProvider, FakeUnitOfWork

pytestmark = pytest.mark.anyio

SENTINEL = "SENTINEL-probe-112-c0ffee"


class _Res(CommandResult):
    r: str


class _Reserve(Command[_Res]):
    skus: list[str]


def _behavior(p: LockProvider) -> AggregateLockingBehavior:
    res = DictLockKeyResolver()
    res.register(_Reserve, lambda c: [f"stock:{s}" for s in c.skus])
    return AggregateLockingBehavior(p, res)


def _ctx(*skus: str) -> MessageContext:
    async def h(c: Any, u: Any) -> _Res:
        return _Res(r="x")

    return MessageContext(message=_Reserve(skus=list(skus)), handler=h)


async def _settle(n: int = 20) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


async def _is_free(p: LockProvider, key: str) -> bool:
    with anyio.move_on_after(0.2):
        await p.acquire(key)
        await p.release(key)
        return True
    return False


# ── P1: a provider that converts the CancelledError into another error ──────


class _ConvertingLocks:
    """Wraps the fake; release awaits I/O and converts a cancel to its own error,
    as some clients do (e.g. wrapping every failure in a ConnectionError)."""

    def __init__(self, slow: str) -> None:
        self.inner = FakeLockProvider()
        self.slow = slow
        self.gate = asyncio.Event()

    async def acquire(self, key: str) -> None:
        await self.inner.acquire(key)

    async def release(self, key: str) -> None:
        if key == self.slow:
            try:
                await self.gate.wait()
            except asyncio.CancelledError as e:
                raise ConnectionError("release interrupted") from e
        await self.inner.release(key)


async def test_p1_converted_cancel_strands_key_and_loses_cancellation() -> None:
    locks = _ConvertingLocks(slow="stock:b")

    async def ok() -> str:
        return "ok"

    t = asyncio.ensure_future(_behavior(locks).handle(_ctx("a", "b", "c"), ok))
    await anyio.wait_all_tasks_blocked()  # stock:c released, stock:b waits
    t.cancel()
    await _settle()
    outcome = "cancelled" if t.cancelled() else repr(t.result())
    # Observed: the command *returns* "ok" although it was cancelled, and stock:b
    # is neither released nor handed over: it stays held.
    stranded = not await _is_free(locks.inner, "stock:b")
    print("P1 outcome:", outcome, "stock:b stranded:", stranded)
    assert outcome == "'ok'" and stranded  # documents the defect


# ── P2: the retried release frees a lock another holder now has ─────────────


class _InstrumentedLocks:
    """A decorator provider (metrics/tracing) that awaits after the inner release.

    A cancel delivered at that await interrupts a release whose effect already
    happened; that is exactly a network release whose reply read is cancelled.
    """

    def __init__(self, inner: FakeLockProvider) -> None:
        self.inner = inner
        self.after = asyncio.Event()

    async def acquire(self, key: str) -> None:
        await self.inner.acquire(key)

    async def release(self, key: str) -> None:
        await self.inner.release(key)
        await self.after.wait()  # e.g. await metrics.flush()


async def test_p2_retried_release_breaks_mutual_exclusion() -> None:
    fake = FakeLockProvider()
    locks = _InstrumentedLocks(fake)
    holder_inside = asyncio.Event()
    leave = asyncio.Event()
    third_got_in = False

    async def ok() -> str:
        return "ok"

    async def second_holder() -> None:
        await fake.acquire("stock:a")
        holder_inside.set()
        await leave.wait()
        await fake.release("stock:a")

    async def third_holder() -> None:
        nonlocal third_got_in
        await fake.acquire("stock:a")
        third_got_in = True
        await fake.release("stock:a")

    t = asyncio.ensure_future(_behavior(locks).handle(_ctx("a"), ok))
    await anyio.wait_all_tasks_blocked()  # stock:a released, waits after
    s = asyncio.ensure_future(second_holder())
    await asyncio.sleep(0)
    t.cancel()  # interrupted after the effect: the key is retried in background
    with pytest.raises(asyncio.CancelledError):
        await t
    await holder_inside.wait()
    locks.after.set()
    await _settle()
    th = asyncio.ensure_future(third_holder())
    await _settle()
    entered_while_held = third_got_in
    print("P2 third holder entered while second holds:", entered_while_held)
    leave.set()
    await _settle()
    await asyncio.gather(s, th, return_exceptions=True)
    assert not entered_while_held  # fixed at 2686dbe (B-68)


# ── P3: self-cancel pending at the snapshot: interrupted key is stranded ────


class _SlowLocks:
    def __init__(self) -> None:
        self.inner = FakeLockProvider()

    async def acquire(self, key: str) -> None:
        await self.inner.acquire(key)

    async def release(self, key: str) -> None:
        await asyncio.sleep(0)  # one suspension point
        await self.inner.release(key)


async def test_p3_pending_cancel_at_snapshot_strands_key() -> None:
    locks = _SlowLocks()

    async def self_cancelling() -> str:
        task = asyncio.current_task()
        assert task is not None
        task.cancel()  # requested, not yet delivered
        return "ok"

    t = asyncio.ensure_future(_behavior(locks).handle(_ctx("a", "b"), self_cancelling))
    with pytest.raises(asyncio.CancelledError):
        await t
    await _settle()
    free_b = await _is_free(locks.inner, "stock:b")
    free_a = await _is_free(locks.inner, "stock:a")
    print("P3 stock:b free:", free_b, "stock:a free:", free_a)
    # Observed: stock:b (interrupted) is neither released nor handed over.
    assert not free_b and free_a


# ── P4: nested asyncio.timeout around dispatch, timeout during releases ─────


async def test_p4_asyncio_timeout_during_release_becomes_timeout_error() -> None:
    gate = asyncio.Event()

    class _Gated(_SlowLocks):
        async def release(self, key: str) -> None:
            await gate.wait()
            await self.inner.release(key)

    locks = _Gated()

    async def ok() -> str:
        return "ok"

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.02):
            await _behavior(locks).handle(_ctx("a", "b"), ok)
    task = asyncio.current_task()
    assert task is not None and task.cancelling() == 0
    gate.set()
    await _settle()
    assert await _is_free(locks.inner, "stock:a")
    assert await _is_free(locks.inner, "stock:b")


async def test_p4b_nested_timeouts_inner_fires_in_handler_outer_in_release() -> None:
    gate = asyncio.Event()

    class _Gated(_SlowLocks):
        async def release(self, key: str) -> None:
            await gate.wait()
            await self.inner.release(key)

    locks = _Gated()

    async def slow() -> str:
        async with asyncio.timeout(0.01):  # inner timeout inside the handler
            await asyncio.sleep(1)
        return "never"

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):  # outer fires while releases wait
            await _behavior(locks).handle(_ctx("a", "b"), slow)
    gate.set()
    await _settle()
    assert await _is_free(locks.inner, "stock:a")
    assert await _is_free(locks.inner, "stock:b")


# ── P5: provider that swallows CancelledError ────────────────────────────────


async def test_p5_swallowing_provider_loses_cancellation_but_releases_all() -> None:
    gate = asyncio.Event()

    class _Swallow(_SlowLocks):
        async def release(self, key: str) -> None:
            if key == "stock:b":
                try:
                    await gate.wait()
                except asyncio.CancelledError:
                    pass  # swallowed
            await self.inner.release(key)

    locks = _Swallow()

    async def ok() -> str:
        return "ok"

    t = asyncio.ensure_future(_behavior(locks).handle(_ctx("a", "b"), ok))
    await anyio.wait_all_tasks_blocked()
    t.cancel()
    await _settle()
    print("P5 task cancelled:", t.cancelled())
    assert await _is_free(locks.inner, "stock:a")
    assert await _is_free(locks.inner, "stock:b")


# ── P6: _HANDED_OVER lifetime: loop closed with a pending background task ────


def test_p6_handed_over_entry_outlives_a_closed_loop() -> None:
    before = len(B._HANDED_OVER)

    class _Never:
        async def acquire(self, key: str) -> None: ...

        async def release(self, key: str) -> None:
            await asyncio.Event().wait()

    async def main() -> None:
        B._hand_over(_Never(), [f"stock:{SENTINEL}"], "_Reserve")
        await asyncio.sleep(0)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(main())
    loop.close()  # old-style shutdown, no task cancellation
    gc.collect()
    leaked = len(B._HANDED_OVER) - before
    print("P6 entries left after loop.close():", leaked)
    # clean up for later probes
    for t in list(B._HANDED_OVER):
        if t.get_loop() is loop:
            B._HANDED_OVER.pop(t)
    assert leaked == 1


def test_p6b_asyncio_run_shutdown_logs_interrupted_background_release(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="pydomain.pipeline")
    gate_holder: dict[str, asyncio.Event] = {}

    class _Gated:
        def __init__(self) -> None:
            self.inner = FakeLockProvider()

        async def acquire(self, key: str) -> None:
            await self.inner.acquire(key)

        async def release(self, key: str) -> None:
            await gate_holder["g"].wait()
            await self.inner.release(key)

    async def main() -> None:
        gate_holder["g"] = asyncio.Event()
        locks = _Gated()

        async def ok() -> str:
            return "ok"

        t = asyncio.ensure_future(_behavior(locks).handle(_ctx("a"), ok))
        await anyio.wait_all_tasks_blocked()
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        # main returns: asyncio.run cancels the background task

    asyncio.run(main())
    msgs = [r.getMessage() for r in caplog.records]
    print("P6b log:", msgs)
    assert msgs == ["Abandoned lock release for _Reserve failed with CancelledError"]


# ── P7: fake with multiple tasks; release from another task ─────────────────


async def test_p7_other_task_waits_not_raises_and_cross_task_release() -> None:
    fake = FakeLockProvider()
    await fake.acquire("k")
    got = asyncio.Event()

    async def other() -> None:
        await fake.acquire("k")  # different task: must wait, not raise
        got.set()

    o = asyncio.ensure_future(other())
    await anyio.wait_all_tasks_blocked()
    assert not got.is_set()

    async def releaser() -> None:
        await fake.release("k")  # release from a third task

    await asyncio.ensure_future(releaser())
    await o
    assert got.is_set()
    # holder is now `o` (finished); the original task may acquire again (waits/OK)
    # but `o` never released: the original task must block, not raise reentry.
    with anyio.move_on_after(0.05) as scope:
        await fake.acquire("k")
    assert scope.cancelled_caught
    await fake.release("k")
    # after the release, this task re-acquires and a second acquire raises
    await fake.acquire("k")
    with pytest.raises(LockReentryError):
        await fake.acquire("k")
    await fake.release("k")


async def test_p7b_reentry_message_and_logs_have_no_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    fake = FakeLockProvider()
    await fake.acquire(SENTINEL)
    with pytest.raises(LockReentryError) as e:
        await fake.acquire(SENTINEL)
    assert SENTINEL not in str(e.value) and SENTINEL not in repr(e.value)
    await fake.release(SENTINEL)
    assert SENTINEL not in caplog.text


# ── P8: content sentinel across every failure path, notes and logs ──────────


@pytest.mark.parametrize("handler_fails", [False, True])
@pytest.mark.parametrize("fatal", [False, True])
async def test_p8_sentinel_absent_from_notes_logs_and_messages(
    caplog: pytest.LogCaptureFixture, handler_fails: bool, fatal: bool
) -> None:
    caplog.set_level(logging.DEBUG)

    class _Fatal(BaseException):
        pass

    class _Failing:
        def __init__(self) -> None:
            self.inner = FakeLockProvider()

        async def acquire(self, key: str) -> None:
            await self.inner.acquire(key)

        async def release(self, key: str) -> None:
            if key.endswith("x"):
                raise (_Fatal(SENTINEL + key) if fatal else KeyError(SENTINEL + key))
            await self.inner.release(key)

    async def h() -> str:
        if handler_fails:
            raise ValueError("synthetic")
        return "ok"

    err: BaseException | None = None
    try:
        await _behavior(_Failing()).handle(_ctx(SENTINEL + "x", SENTINEL + "y"), h)
    except BaseException as e:  # noqa: BLE001
        err = e
    texts = [caplog.text]
    if err is not None:
        texts += list(getattr(err, "__notes__", []))
        if not fatal:  # a re-raised provider BaseException keeps its own text
            texts += [str(err), repr(err)]
    assert all(SENTINEL not in t for t in texts), texts


# ── P9: anyio scope + task-owned provider whose release awaits: always handed over


async def test_p9_anyio_scope_hands_every_suspending_release_over() -> None:
    class _Owned:
        def __init__(self) -> None:
            self.inner = FakeLockProvider()
            self.owner: dict[str, Any] = {}
            self.refused: list[str] = []

        async def acquire(self, key: str) -> None:
            await self.inner.acquire(key)
            self.owner[key] = asyncio.current_task()

        async def release(self, key: str) -> None:
            await asyncio.sleep(0)  # any network round-trip
            if asyncio.current_task() is not self.owner[key]:
                self.refused.append(key)
                raise RuntimeError("not owner")
            await self.inner.release(key)

    locks = _Owned()

    async def h() -> str:
        await anyio.sleep_forever()
        return "x"

    with anyio.move_on_after(0.01):
        await _behavior(locks).handle(_ctx("a", "b"), h)
    await _settle()
    print("P9 refused:", locks.refused)
    assert locks.refused == ["stock:b", "stock:a"]


# ── P10: reverse order preserved across the hand-over; each key once ─────────


async def test_p10_order_and_once_per_key_across_hand_over() -> None:
    gate = asyncio.Event()
    calls: list[str] = []

    class _Rec(_SlowLocks):
        async def release(self, key: str) -> None:
            calls.append(key)
            if key == "stock:c":
                await gate.wait()
            await self.inner.release(key)

    locks = _Rec()

    async def ok() -> str:
        return "ok"

    t = asyncio.ensure_future(_behavior(locks).handle(_ctx("a", "b", "c", "d"), ok))
    await anyio.wait_all_tasks_blocked()
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    gate.set()
    await _settle()
    assert calls == ["stock:d", "stock:c", "stock:c", "stock:b", "stock:a"]
    assert not B._HANDED_OVER  # reference dropped once done


# ── P12: acquire's wait completes, then the cancel lands before it resumes ──


async def test_p12_cancel_after_acquire_future_resolved_before_resume() -> None:
    fake = FakeLockProvider()
    bus = CommandBus()

    async def h(c: Any, u: Any) -> _Res:
        return _Res(r="x")

    bus.register(_Reserve, h, uow_factory=lambda: FakeUnitOfWork(),
                 behaviors=[_behavior(fake)])
    await fake.acquire("stock:b")
    d = asyncio.ensure_future(bus.dispatch(_Reserve(skus=["a", "b"])))
    await anyio.wait_all_tasks_blocked()  # holds stock:a, waits on stock:b
    await fake.release("stock:b")  # resolves d's waiter future...
    d.cancel()  # ...and the cancel lands before d resumes
    with pytest.raises(asyncio.CancelledError):
        await d
    assert await _is_free(fake, "stock:a")
    assert await _is_free(fake, "stock:b")
