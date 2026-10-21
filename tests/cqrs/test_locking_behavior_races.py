"""Cancellation races in ``AggregateLockingBehavior`` (DCE-123, ADR-076, B-51).

These come from the PR #112 review probes: timeouts and anyio scopes that fire
during the releases, a cancel that lands after an acquire resolved, the order
of the keys across the hand-over, and the text of every failure path.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import anyio
import pytest

from pydomain.cqrs import Command, CommandBus, CommandResult
from pydomain.cqrs.behaviors import AggregateLockingBehavior, MessageContext
from pydomain.cqrs.locking import DictLockKeyResolver, LockProvider
from pydomain.testing import FakeLockProvider, FakeUnitOfWork
from tests.support.content_sentinel import CONTENT_SENTINEL, assert_absent_from_logs

pytestmark = pytest.mark.anyio

_TIMEOUT_SECONDS = 1


class _Reserved(CommandResult):
    reservation: str


class _ReserveStock(Command[_Reserved]):
    """Synthetic command that locks one key per stock-keeping unit."""

    skus: list[str]


def _behavior(provider: LockProvider) -> AggregateLockingBehavior:
    resolver = DictLockKeyResolver()
    resolver.register(
        _ReserveStock, lambda command: [f"stock:{sku}" for sku in command.skus]
    )
    return AggregateLockingBehavior(provider, resolver)


async def _reserve(command: _ReserveStock, uow: Any) -> _Reserved:
    return _Reserved(reservation="reservation-1")


def _ctx(*skus: str) -> MessageContext:
    return MessageContext(message=_ReserveStock(skus=list(skus)), handler=_reserve)


async def _ok() -> str:
    return "reserved"


async def _settle() -> None:
    """Let background tasks run to completion."""
    for _ in range(20):
        await asyncio.sleep(0)


async def _is_free(locks: LockProvider, key: str) -> None:
    """Acquire and release *key*; a key still held fails the test by timeout."""
    with anyio.fail_after(_TIMEOUT_SECONDS):
        await locks.acquire(key)
        await locks.release(key)


class _GatedLocks:
    """A ``FakeLockProvider`` whose releases wait on a gate (all keys, or one)."""

    def __init__(self, *, gated_key: str | None = None) -> None:
        self.inner = FakeLockProvider()
        self.gate = asyncio.Event()
        self._gated_key = gated_key
        self.calls: list[str] = []

    async def acquire(self, key: str) -> None:
        await self.inner.acquire(key)

    async def release(self, key: str) -> None:
        self.calls.append(key)
        if self._gated_key in (None, key):
            await self.gate.wait()
        await self.inner.release(key)


class TestTimeoutsDuringReleases:
    async def test_asyncio_timeout_during_the_releases_raises_timeout_error(
        self,
    ) -> None:
        """An ``asyncio.timeout`` that fires during a release becomes its
        ``TimeoutError``, the task is not left cancelling, and every key is
        released in the background."""
        locks = _GatedLocks()

        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.02):
                await _behavior(locks).handle(_ctx("a", "b"), _ok)

        task = asyncio.current_task()
        assert task is not None and task.cancelling() == 0
        locks.gate.set()
        await _settle()
        await _is_free(locks.inner, "stock:a")
        await _is_free(locks.inner, "stock:b")

    async def test_nested_timeouts_inner_in_handler_outer_during_releases(
        self,
    ) -> None:
        locks = _GatedLocks()

        async def slow() -> str:
            async with asyncio.timeout(0.01):  # fires inside the handler
                await asyncio.sleep(1)
            return "never"

        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):  # fires while the releases wait
                await _behavior(locks).handle(_ctx("a", "b"), slow)

        locks.gate.set()
        await _settle()
        await _is_free(locks.inner, "stock:a")
        await _is_free(locks.inner, "stock:b")


class TestHandOver:
    async def test_order_is_kept_and_only_the_interrupted_key_is_retried(
        self,
    ) -> None:
        locks = _GatedLocks(gated_key="stock:c")

        handling = asyncio.ensure_future(
            _behavior(locks).handle(_ctx("a", "b", "c", "d"), _ok)
        )
        await anyio.wait_all_tasks_blocked()  # stock:d released, stock:c waits
        handling.cancel()
        with pytest.raises(asyncio.CancelledError):
            await handling
        locks.gate.set()
        await _settle()

        assert locks.calls == ["stock:d", "stock:c", "stock:c", "stock:b", "stock:a"]
        for key in ("stock:a", "stock:b", "stock:c", "stock:d"):
            await _is_free(locks.inner, key)

    async def test_anyio_scope_hands_every_suspending_release_to_the_background(
        self,
    ) -> None:
        """Under a cancelled anyio scope every release that suspends is
        interrupted, so all of them move to the background task, where a
        task-owned provider refuses them (the documented limit, ADR-076)."""

        class _Owned:
            def __init__(self) -> None:
                self.inner = FakeLockProvider()
                self.owner: dict[str, Any] = {}
                self.refused: list[str] = []

            async def acquire(self, key: str) -> None:
                await self.inner.acquire(key)
                self.owner[key] = asyncio.current_task()

            async def release(self, key: str) -> None:
                await asyncio.sleep(0)  # a network round trip
                if asyncio.current_task() is not self.owner[key]:
                    self.refused.append(key)
                    raise RuntimeError("not the owner")
                await self.inner.release(key)

        locks = _Owned()

        async def handler() -> str:
            await anyio.sleep_forever()
            raise AssertionError("unreachable")

        with anyio.move_on_after(0.01):
            await _behavior(locks).handle(_ctx("a", "b"), handler)
        await _settle()

        assert locks.refused == ["stock:b", "stock:a"]

    async def test_cancel_after_an_acquire_resolved_releases_both_keys(self) -> None:
        """The second acquire's wait completes, then the cancel lands before the
        task resumes: the lock it was granted is released too."""
        locks = FakeLockProvider()
        bus = CommandBus()
        bus.register(
            _ReserveStock,
            _reserve,
            uow_factory=lambda: FakeUnitOfWork(),
            behaviors=[_behavior(locks)],
        )
        await locks.acquire("stock:b")
        dispatch = asyncio.ensure_future(bus.dispatch(_ReserveStock(skus=["a", "b"])))
        await anyio.wait_all_tasks_blocked()  # holds stock:a, waits on stock:b
        await locks.release("stock:b")  # resolves the waiter...
        dispatch.cancel()  # ...and the cancel lands before it resumes
        with pytest.raises(asyncio.CancelledError):
            await dispatch

        await _is_free(locks, "stock:a")
        await _is_free(locks, "stock:b")


class _Fatal(BaseException):
    """Synthetic ``BaseException`` that is not an ``Exception``."""


@pytest.mark.parametrize("handler_fails", [False, True], ids=["ok", "failed"])
@pytest.mark.parametrize("fatal", [False, True], ids=["error", "fatal"])
async def test_no_key_or_message_text_in_notes_or_logs(
    caplog: pytest.LogCaptureFixture, handler_fails: bool, fatal: bool
) -> None:
    """Every failure path names types only: no lock key and no exception text
    reaches a note or a log line (ADR-071)."""
    caplog.set_level(logging.DEBUG)

    class _Failing:
        def __init__(self) -> None:
            self.inner = FakeLockProvider()

        async def acquire(self, key: str) -> None:
            await self.inner.acquire(key)

        async def release(self, key: str) -> None:
            if key.endswith("x"):
                text = CONTENT_SENTINEL + key
                raise _Fatal(text) if fatal else KeyError(text)
            await self.inner.release(key)

    async def handler() -> str:
        if handler_fails:
            raise ValueError("synthetic refusal")
        return "reserved"

    error: BaseException | None = None
    try:
        await _behavior(_Failing()).handle(
            _ctx(CONTENT_SENTINEL + "x", CONTENT_SENTINEL + "y"), handler
        )
    except (ValueError, _Fatal) as raised:
        error = raised

    assert_absent_from_logs(caplog)
    if error is not None:
        assert all(CONTENT_SENTINEL not in n for n in getattr(error, "__notes__", []))
        if not fatal:  # a re-raised provider BaseException keeps its own text
            assert CONTENT_SENTINEL not in str(error) + repr(error)


def test_shutdown_logs_the_interrupted_background_release(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``asyncio.run`` cancels a background release that is still waiting at
    shutdown; it is logged by type only."""
    caplog.set_level(logging.WARNING, logger="pydomain.pipeline")

    async def main() -> None:
        locks = _GatedLocks()
        handling = asyncio.ensure_future(_behavior(locks).handle(_ctx("a"), _ok))
        await anyio.wait_all_tasks_blocked()
        handling.cancel()
        with pytest.raises(asyncio.CancelledError):
            await handling
        # main returns: asyncio.run cancels the background task

    asyncio.run(main())

    assert [r.getMessage() for r in caplog.records] == [
        "Abandoned lock release for _ReserveStock failed with CancelledError"
    ]


class TestCancellationThroughTheProvider:
    """⑨ 3, 4: a cancel the provider converts, swallows, or that was requested
    before the releases began still hands the rest over and propagates."""

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-123 B-51: a converted cancel is taken for a failed release",
    )
    async def test_provider_that_converts_the_cancel_still_hands_over(self) -> None:
        class _Converting(_GatedLocks):
            async def release(self, key: str) -> None:
                try:
                    await super().release(key)
                except asyncio.CancelledError as cancelled:
                    raise ConnectionError("release interrupted") from cancelled

        locks = _Converting(gated_key="stock:b")
        handling = asyncio.ensure_future(
            _behavior(locks).handle(_ctx("a", "b", "c"), _ok)
        )
        await anyio.wait_all_tasks_blocked()  # stock:c released, stock:b waits
        handling.cancel()
        await _settle()

        assert handling.cancelled()
        locks.gate.set()
        await _settle()
        await _is_free(locks.inner, "stock:b")
        await _is_free(locks.inner, "stock:a")

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-123 B-51: a swallowed cancel is lost",
    )
    async def test_provider_that_swallows_the_cancel_still_propagates_it(
        self,
    ) -> None:
        class _Swallowing(_GatedLocks):
            async def release(self, key: str) -> None:
                if key == "stock:b":
                    try:
                        await self.gate.wait()
                    except asyncio.CancelledError:
                        pass  # swallowed
                await self.inner.release(key)

        locks = _Swallowing(gated_key="stock:b")
        handling = asyncio.ensure_future(_behavior(locks).handle(_ctx("a", "b"), _ok))
        await anyio.wait_all_tasks_blocked()
        handling.cancel()
        await _settle()

        assert handling.cancelled()
        await _is_free(locks.inner, "stock:b")
        await _is_free(locks.inner, "stock:a")

    @pytest.mark.xfail(
        strict=True,
        raises=TimeoutError,
        reason="DCE-123 B-51: a cancel pending before the releases strands a key",
    )
    async def test_cancel_requested_before_the_releases_hands_over(self) -> None:
        class _Slow:
            def __init__(self) -> None:
                self.inner = FakeLockProvider()

            async def acquire(self, key: str) -> None:
                await self.inner.acquire(key)

            async def release(self, key: str) -> None:
                await asyncio.sleep(0)  # one suspension point
                await self.inner.release(key)

        locks = _Slow()

        async def self_cancelling() -> str:
            task = asyncio.current_task()
            assert task is not None
            task.cancel()  # requested, delivered at the first release's await
            return "reserved"

        handling = asyncio.ensure_future(
            _behavior(locks).handle(_ctx("a", "b"), self_cancelling)
        )
        with pytest.raises(asyncio.CancelledError):
            await handling
        await _settle()

        await _is_free(locks.inner, "stock:b")
        await _is_free(locks.inner, "stock:a")


class TestRetriedReleaseIsOwnershipSafe:
    """B-51, B-68: the retried release never frees a lock another holder took."""

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-123 B-68: the retried release frees the next holder's lock",
    )
    async def test_retried_release_does_not_free_the_next_holders_lock(
        self,
    ) -> None:
        fake = FakeLockProvider()

        class _Instrumented:
            """Awaits after the release took effect, like a reply in flight."""

            def __init__(self) -> None:
                self.after = asyncio.Event()

            async def acquire(self, key: str) -> None:
                await fake.acquire(key)

            async def release(self, key: str) -> None:
                await fake.release(key)
                await self.after.wait()

        locks = _Instrumented()
        second_inside = asyncio.Event()
        second_leave = asyncio.Event()
        third_inside = False

        async def second_holder() -> None:
            await fake.acquire("stock:a")
            second_inside.set()
            await second_leave.wait()
            await fake.release("stock:a")

        async def third_holder() -> None:
            nonlocal third_inside
            await fake.acquire("stock:a")
            third_inside = True
            await fake.release("stock:a")

        handling = asyncio.ensure_future(_behavior(locks).handle(_ctx("a"), _ok))
        await anyio.wait_all_tasks_blocked()  # stock:a released, reply pending
        second = asyncio.ensure_future(second_holder())
        await second_inside.wait()
        handling.cancel()  # interrupted after the effect: retried in background
        with pytest.raises(asyncio.CancelledError):
            await handling
        locks.after.set()
        await _settle()
        third = asyncio.ensure_future(third_holder())
        await _settle()

        try:
            assert not third_inside, "two holders of stock:a at once"
        finally:
            second_leave.set()
            await asyncio.gather(second, third, return_exceptions=True)


@pytest.mark.xfail(
    strict=True,
    raises=RuntimeError,
    reason="DCE-123 B-69: without an asyncio task the release loop raises",
)
def test_releases_run_inline_without_an_asyncio_task() -> None:
    """Under another backend (trio) no asyncio task runs: the releases run
    inline, in order, with no hand-over (B-69). Driven by hand, without a loop."""

    class _Plain:
        def __init__(self) -> None:
            self.released: list[str] = []

        async def acquire(self, key: str) -> None:
            return None

        async def release(self, key: str) -> None:
            self.released.append(key)

    locks = _Plain()
    coroutine = _behavior(locks).handle(_ctx("a", "b"), _ok)
    with pytest.raises(StopIteration) as finished:
        coroutine.send(None)

    assert finished.value.value == "reserved"
    assert locks.released == ["stock:b", "stock:a"]
