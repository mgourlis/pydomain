"""Tests for OrderingSpy — one shared ordered log across collaborators."""

from __future__ import annotations

import asyncio

import pytest

from tests.support.fault_injection import FaultInjector
from tests.support.ordering import OrderingSpy

pytestmark = pytest.mark.anyio


class _Outbox:
    async def append(self, entry: str) -> None:
        pass

    async def fail(self) -> None:
        raise RuntimeError("synthetic outage")

    def size(self) -> int:
        return 0


class _Locks:
    async def acquire(self, key: str) -> None:
        pass

    async def release(self, key: str) -> None:
        pass


async def test_record_appends_in_order() -> None:
    spy = OrderingSpy()

    spy.record("commit")
    spy.record("publish")

    assert spy.entries == ("commit", "publish")


async def test_wrapped_calls_share_one_log() -> None:
    spy = OrderingSpy()
    outbox = spy.wrap(_Outbox(), "outbox")
    locks = spy.wrap(_Locks(), "lock")

    await locks.acquire("order-1")
    await outbox.append("entry")
    spy.record("commit")
    await locks.release("order-1")

    assert spy.entries == ("lock.acquire", "outbox.append", "commit", "lock.release")


async def test_raising_call_is_recorded_and_propagates() -> None:
    spy = OrderingSpy()
    outbox = spy.wrap(_Outbox(), "outbox")

    with pytest.raises(RuntimeError):
        await outbox.fail()

    assert spy.entries == ("outbox.fail raised RuntimeError",)


async def test_sync_methods_are_not_recorded() -> None:
    spy = OrderingSpy()
    outbox = spy.wrap(_Outbox(), "outbox")

    assert outbox.size() == 0
    assert spy.entries == ()


async def test_methods_limits_what_is_recorded() -> None:
    spy = OrderingSpy()
    locks = spy.wrap(_Locks(), "lock", methods={"release"})

    await locks.acquire("order-1")
    await locks.release("order-1")

    assert spy.entries == ("lock.release",)


async def test_assert_order_allows_entries_in_between() -> None:
    spy = OrderingSpy()
    for entry in ("lock.acquire", "commit", "idempotency.set", "lock.release"):
        spy.record(entry)

    spy.assert_order("commit", "lock.release")


async def test_assert_order_fails_when_out_of_order() -> None:
    spy = OrderingSpy()
    spy.record("lock.release")
    spy.record("commit")

    with pytest.raises(AssertionError):
        spy.assert_order("commit", "lock.release")


async def test_assert_order_fails_when_an_entry_is_missing() -> None:
    spy = OrderingSpy()
    spy.record("commit")

    with pytest.raises(AssertionError):
        spy.assert_order("commit", "publish")


async def test_assert_order_counts_repeated_entries() -> None:
    spy = OrderingSpy()
    spy.record("publish")

    with pytest.raises(AssertionError):
        spy.assert_order("publish", "publish")


async def test_assert_entries_requires_the_exact_log() -> None:
    spy = OrderingSpy()
    spy.record("commit")
    spy.record("publish")

    spy.assert_entries("commit", "publish")
    with pytest.raises(AssertionError):
        spy.assert_entries("commit")


async def test_wraps_a_fault_injector_proxy() -> None:
    spy = OrderingSpy()
    faulty = FaultInjector(_Locks(), at=2, raises=asyncio.CancelledError)
    locks = spy.wrap(faulty.proxy, "lock")

    await locks.acquire("order-1")
    with pytest.raises(asyncio.CancelledError):
        await locks.release("order-1")

    assert spy.entries == ("lock.acquire", "lock.release raised CancelledError")


class _Stream:
    def __init__(self, *items: str) -> None:
        self.items = list(items)

    def __aiter__(self) -> _Stream:
        return self

    async def __anext__(self) -> str:
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)


async def test_async_iteration_records_each_anext() -> None:
    spy = OrderingSpy()
    stream = spy.wrap(_Stream("P-1"), "feed")

    assert [item async for item in stream] == ["P-1"]

    assert spy.entries == ("feed.__anext__", "feed.__anext__ raised StopAsyncIteration")


async def test_async_iteration_through_a_fault_injector_proxy() -> None:
    spy = OrderingSpy()
    faulty = FaultInjector(_Stream("P-1", "P-2"), at=2, raises=ValueError)
    stream = spy.wrap(faulty.proxy, "feed")
    seen: list[str] = []

    with pytest.raises(ValueError):
        async for item in stream:
            seen.append(item)

    assert seen == ["P-1"]
    assert spy.entries == ("feed.__anext__", "feed.__anext__ raised ValueError")
