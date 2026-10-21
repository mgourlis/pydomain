"""Lock release guarantees of ``AggregateLockingBehavior`` (DCE-123, ADR-076).

Every lock the behavior acquired is released, even when a later ``acquire``
is cancelled (CQRS-01) or another ``release`` raises (CQRS-02), and a failed
release after a successful handler does not fail the command.
"""

from __future__ import annotations

import asyncio
from typing import Any

import anyio
import pytest

from pydomain.cqrs import Command, CommandBus, CommandResult
from pydomain.cqrs.behaviors import AggregateLockingBehavior, MessageContext
from pydomain.cqrs.exceptions import CommandExecutionError, LockReentryError
from pydomain.cqrs.locking import DictLockKeyResolver, LockProvider
from pydomain.testing import FakeLockProvider, FakeUnitOfWork
from tests.support.content_sentinel import CONTENT_SENTINEL, assert_absent_from_logs
from tests.support.fault_injection import FaultInjector
from tests.support.ordering import OrderingSpy

pytestmark = pytest.mark.anyio

_TIMEOUT_SECONDS = 1


class _Reserved(CommandResult):
    reservation: str


class _ReserveStock(Command[_Reserved]):
    """Synthetic command that locks one key per stock-keeping unit."""

    skus: list[str]


class _ReleaseStock(Command[_Reserved]):
    """Synthetic command dispatched from inside another command's handler."""


class _LockLog:
    """Wrap a ``LockProvider``; record each ``acquire`` and ``release`` that returns."""

    def __init__(self, inner: LockProvider) -> None:
        self._inner = inner
        self.calls: list[tuple[str, str]] = []

    async def acquire(self, key: str) -> None:
        await self._inner.acquire(key)
        self.calls.append(("acquire", key))

    async def release(self, key: str) -> None:
        await self._inner.release(key)
        self.calls.append(("release", key))


def _behavior(provider: LockProvider) -> AggregateLockingBehavior:
    resolver = DictLockKeyResolver()
    resolver.register(
        _ReserveStock, lambda command: [f"stock:{sku}" for sku in command.skus]
    )
    return AggregateLockingBehavior(provider, resolver)


async def _reserve(command: _ReserveStock, uow: Any) -> _Reserved:
    return _Reserved(reservation="reservation-1")


async def _is_free(locks: LockProvider, key: str) -> None:
    """Acquire and release *key*; a key still held fails the test by timeout."""
    with anyio.fail_after(_TIMEOUT_SECONDS):
        await locks.acquire(key)
        await locks.release(key)


class TestReleaseUnderCancellation:
    async def test_locks_released_when_acquire_is_cancelled(self) -> None:
        locks = FakeLockProvider()
        log = _LockLog(locks)
        fault = FaultInjector(
            log, at=2, raises=asyncio.CancelledError, methods={"acquire"}
        )
        ctx = MessageContext(message=_ReserveStock(skus=["a", "b"]), handler=_reserve)

        async def handler() -> str:
            raise AssertionError("the handler must not run without its locks")

        with pytest.raises(asyncio.CancelledError):
            await _behavior(fault.proxy).handle(ctx, handler)

        assert log.calls == [("acquire", "stock:a"), ("release", "stock:a")]
        await _is_free(locks, "stock:a")

    async def test_locks_released_when_dispatch_is_cancelled_waiting_for_a_lock(
        self,
    ) -> None:
        locks = FakeLockProvider()
        log = _LockLog(locks)
        bus = CommandBus()
        bus.register(
            _ReserveStock,
            _reserve,
            uow_factory=lambda: FakeUnitOfWork(),
            behaviors=[_behavior(log)],
        )
        await locks.acquire("stock:b")  # another holder of the second key

        dispatch = asyncio.ensure_future(bus.dispatch(_ReserveStock(skus=["a", "b"])))
        await anyio.wait_all_tasks_blocked()  # holds stock:a, waits on stock:b
        dispatch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await dispatch
        await locks.release("stock:b")

        assert log.calls == [("acquire", "stock:a"), ("release", "stock:a")]
        await _is_free(locks, "stock:a")


class TestFailingRelease:
    async def test_failing_release_does_not_strand_other_locks(self) -> None:
        locks = FakeLockProvider()
        log = _LockLog(locks)
        # Released in reverse order: stock:c, then stock:b (fails), then stock:a.
        fault = FaultInjector(log, at=2, raises=ConnectionError, methods={"release"})
        ctx = MessageContext(
            message=_ReserveStock(skus=["a", "b", "c"]), handler=_reserve
        )

        async def handler() -> str:
            return "reserved"

        await _behavior(fault.proxy).handle(ctx, handler)

        assert fault.awaited == ["release"] * 3  # only releases are counted
        assert log.calls[3:] == [("release", "stock:c"), ("release", "stock:a")]
        await _is_free(locks, "stock:a")
        await _is_free(locks, "stock:c")

    async def test_failing_release_does_not_fail_successful_command(self) -> None:
        spy = OrderingSpy()
        fault = FaultInjector(
            FakeLockProvider(), at=1, raises=ConnectionError, methods={"release"}
        )
        bus = CommandBus()
        bus.register(
            _ReserveStock,
            _reserve,
            uow_factory=lambda: spy.wrap(FakeUnitOfWork(), "uow", methods={"commit"}),
            behaviors=[_behavior(fault.proxy)],
        )

        result, _ = await bus.dispatch(_ReserveStock(skus=["a"]))

        assert result == _Reserved(reservation="reservation-1")
        assert fault.fired
        spy.assert_entries("uow.commit")


class _StockRefused(Exception):
    """Synthetic handler failure."""


class _ReleaseAborted(BaseException):
    """Synthetic ``BaseException`` that is not an ``Exception``."""


class _ScriptedLocks:
    """A ``FakeLockProvider`` whose releases can wait on a gate and raise per key.

    A release that raises leaves the key held, as a failed release would.
    """

    def __init__(
        self,
        *,
        gate: asyncio.Event | None = None,
        fail: dict[str, BaseException] | None = None,
    ) -> None:
        self._inner = FakeLockProvider()
        self._gate = gate
        self._fail = fail or {}
        self.released: list[str] = []

    async def acquire(self, key: str) -> None:
        await self._inner.acquire(key)

    async def release(self, key: str) -> None:
        if self._gate is not None:
            await self._gate.wait()
        if key in self._fail:
            raise self._fail[key]
        await self._inner.release(key)
        self.released.append(key)


async def _settle() -> None:
    """Let background tasks run to completion."""
    for _ in range(10):
        await asyncio.sleep(0)


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]


class TestReleaseFailureSurfacing:
    """B-50: how a failed release surfaces; text names types only (ADR-071)."""

    async def test_release_failure_after_success_is_logged_as_a_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level("WARNING", logger="pydomain.pipeline")
        fault = FaultInjector(
            FakeLockProvider(),
            at=1,
            raises=ConnectionError(CONTENT_SENTINEL),
            methods={"release"},
        )
        ctx = MessageContext(
            message=_ReserveStock(skus=[CONTENT_SENTINEL]), handler=_reserve
        )

        async def handler() -> str:
            return "reserved"

        assert await _behavior(fault.proxy).handle(ctx, handler) == "reserved"

        assert _warnings(caplog) == [
            "Lock release failed for _ReserveStock: ConnectionError"
        ]
        assert_absent_from_logs(caplog)

    async def test_release_failures_are_noted_on_the_handler_error(self) -> None:
        locks = _ScriptedLocks(
            fail={
                "stock:c": ConnectionError(CONTENT_SENTINEL),
                "stock:a": TimeoutError(CONTENT_SENTINEL),
            }
        )
        ctx = MessageContext(
            message=_ReserveStock(skus=["a", "b", "c"]), handler=_reserve
        )

        async def handler() -> str:
            raise _StockRefused("synthetic refusal")

        with pytest.raises(_StockRefused) as raised:
            await _behavior(locks).handle(ctx, handler)

        assert raised.value.__notes__ == [
            "clean-up also failed: ConnectionError",
            "clean-up also failed: TimeoutError",
        ]
        assert locks.released == ["stock:b"]

    async def test_base_exception_from_release_is_raised_after_the_other_releases(
        self,
    ) -> None:
        locks = _ScriptedLocks(
            fail={
                "stock:c": ConnectionError(CONTENT_SENTINEL),
                "stock:b": _ReleaseAborted(),
            }
        )
        ctx = MessageContext(
            message=_ReserveStock(skus=["a", "b", "c"]), handler=_reserve
        )

        async def handler() -> str:
            return "reserved"

        with pytest.raises(_ReleaseAborted) as raised:
            await _behavior(locks).handle(ctx, handler)

        assert raised.value.__notes__ == ["clean-up also failed: ConnectionError"]
        assert locks.released == ["stock:a"]


class TestShieldedRelease:
    """B-51: the releases run in one shielded task that a cancel cannot stop."""

    async def test_second_cancellation_does_not_interrupt_the_releases(self) -> None:
        gate = asyncio.Event()
        locks = _ScriptedLocks(gate=gate)
        bus = CommandBus()
        bus.register(
            _ReserveStock,
            _reserve,
            uow_factory=lambda: FakeUnitOfWork(),
            behaviors=[_behavior(locks)],
        )

        dispatch = asyncio.ensure_future(bus.dispatch(_ReserveStock(skus=["a", "b"])))
        await anyio.wait_all_tasks_blocked()  # the releases wait on the gate
        dispatch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await dispatch

        gate.set()
        await _settle()
        assert locks.released == ["stock:b", "stock:a"]

    async def test_anyio_cancel_scope_does_not_interrupt_the_releases(self) -> None:
        gate = asyncio.Event()
        locks = _ScriptedLocks(gate=gate)
        ctx = MessageContext(message=_ReserveStock(skus=["a", "b"]), handler=_reserve)

        async def handler() -> str:
            await anyio.sleep_forever()
            raise AssertionError("unreachable")

        with anyio.move_on_after(0.01) as scope:
            await _behavior(locks).handle(ctx, handler)
        assert scope.cancelled_caught
        assert locks.released == []

        gate.set()
        await _settle()
        assert locks.released == ["stock:b", "stock:a"]

    async def test_abandoned_release_logs_its_failures_by_type(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level("WARNING", logger="pydomain.pipeline")
        gate = asyncio.Event()
        sentinel_key = f"stock:{CONTENT_SENTINEL}"
        locks = _ScriptedLocks(
            gate=gate, fail={sentinel_key: ConnectionError(CONTENT_SENTINEL)}
        )
        ctx = MessageContext(
            message=_ReserveStock(skus=["a", CONTENT_SENTINEL]), handler=_reserve
        )

        async def handler() -> str:
            return "reserved"

        handling = asyncio.ensure_future(_behavior(locks).handle(ctx, handler))
        await anyio.wait_all_tasks_blocked()
        handling.cancel()
        with pytest.raises(asyncio.CancelledError):
            await handling

        gate.set()
        await _settle()
        assert locks.released == ["stock:a"]
        assert _warnings(caplog) == [
            "Abandoned lock release for _ReserveStock failed with ConnectionError"
        ]
        assert_absent_from_logs(caplog)


class TestNestedSameKeyDispatch:
    """B-48, B-52: a same-key nested dispatch fails fast on FakeLockProvider."""

    async def test_nested_same_key_dispatch_raises_lock_reentry_error(self) -> None:
        locks = FakeLockProvider()
        bus = CommandBus()
        resolver = DictLockKeyResolver()
        resolver.register(_ReserveStock, lambda command: ["stock:a"])
        resolver.register(_ReleaseStock, lambda command: ["stock:a"])
        behaviors: list[Any] = [AggregateLockingBehavior(locks, resolver)]

        async def release_stock(command: _ReleaseStock, uow: Any) -> _Reserved:
            return _Reserved(reservation="none")

        async def reserve_then_release(command: _ReserveStock, uow: Any) -> _Reserved:
            await bus.dispatch(_ReleaseStock())  # nested, same key, same task
            return _Reserved(reservation="reservation-1")

        bus.register(
            _ReserveStock,
            reserve_then_release,
            uow_factory=lambda: FakeUnitOfWork(),
            behaviors=behaviors,
        )
        bus.register(
            _ReleaseStock,
            release_stock,
            uow_factory=lambda: FakeUnitOfWork(),
            behaviors=behaviors,
        )

        with anyio.fail_after(_TIMEOUT_SECONDS):
            with pytest.raises(CommandExecutionError) as raised:
                await bus.dispatch(_ReserveStock(skus=["a"]))

        inner = raised.value.__cause__
        assert isinstance(inner, CommandExecutionError)
        assert isinstance(inner.__cause__, LockReentryError)
        await _is_free(locks, "stock:a")
