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
from pydomain.cqrs.exceptions import CommandExecutionError
from pydomain.cqrs.locking import DictLockKeyResolver, LockProvider
from pydomain.testing import FakeLockProvider, FakeUnitOfWork
from tests.support.fault_injection import FaultInjector
from tests.support.ordering import OrderingSpy

pytestmark = pytest.mark.anyio

_TIMEOUT_SECONDS = 1


class _Reserved(CommandResult):
    reservation: str


class _ReserveStock(Command[_Reserved]):
    """Synthetic command that locks one key per stock-keeping unit."""

    skus: list[str]


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
    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-123 CQRS-01: except Exception misses CancelledError; lock 1 kept",
    )
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

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-123 CQRS-01: a dispatch cancelled waiting on lock 2 keeps lock 1",
    )
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
    @pytest.mark.xfail(
        strict=True,
        raises=ConnectionError,
        reason="DCE-123 CQRS-02: one failing release strands the later locks",
    )
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

        assert fault.awaited == ["acquire"] * 3 + ["release"] * 3
        assert log.calls[3:] == [("release", "stock:c"), ("release", "stock:a")]
        await _is_free(locks, "stock:a")
        await _is_free(locks, "stock:c")

    @pytest.mark.xfail(
        strict=True,
        raises=CommandExecutionError,
        reason="DCE-123 CQRS-02: a failing release rolls a successful command back",
    )
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
