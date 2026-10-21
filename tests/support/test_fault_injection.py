"""Tests for FaultInjector — raise or cancel at the Nth awaited call."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

import pytest

from tests.support.fault_injection import FaultInjector

pytestmark = pytest.mark.anyio


class _ShipmentStore:
    """Synthetic collaborator with async and sync methods."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.label = "shipments"

    async def save(self, shipment_id: str) -> str:
        self.calls.append(f"save {shipment_id}")
        return shipment_id

    async def load(self) -> str:
        self.calls.append("load")
        return "loaded"

    def count(self) -> int:
        return len(self.calls)


class _Session:
    """Synthetic async context manager."""

    def __init__(self) -> None:
        self.entered = False

    async def __aenter__(self) -> _Session:
        self.entered = True
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        self.entered = False

    async def save(self) -> str:
        return "saved"


class _Opener:
    """Synthetic async context manager whose ``__aenter__`` returns another object."""

    def __init__(self) -> None:
        self.session = _Session()

    async def __aenter__(self) -> _Session:
        return self.session

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


class _Stream:
    """Synthetic async iterator that is its own iterator."""

    def __init__(self, *items: str) -> None:
        self.items = list(items)

    def __aiter__(self) -> _Stream:
        return self

    async def __anext__(self) -> str:
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)


class _Feed:
    """Synthetic async iterable whose ``__aiter__`` returns a separate iterator."""

    def __init__(self, *items: str) -> None:
        self.items = items

    def __aiter__(self) -> _Stream:
        return _Stream(*self.items)


class _GeneratedFeed:
    """Synthetic async iterable whose ``__aiter__`` is an async generator."""

    async def __aiter__(self) -> AsyncIterator[str]:
        yield "P-1"
        yield "P-2"


_AENTER_ESCAPES = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-108 review: `async with proxy as x` binds the raw target",
)
_NO_ASYNC_ITERATION = pytest.mark.xfail(
    strict=True,
    raises=TypeError,
    reason="DCE-108 review: the proxy does not forward __aiter__/__anext__",
)


async def test_nth_awaited_call_raises_instead_of_reaching_target() -> None:
    store = _ShipmentStore()
    faulty = FaultInjector(store, at=2, raises=ValueError)

    assert await faulty.proxy.save("S-1") == "S-1"
    with pytest.raises(ValueError):
        await faulty.proxy.save("S-2")

    assert store.calls == ["save S-1"]
    assert faulty.fired


async def test_calls_are_counted_across_methods() -> None:
    store = _ShipmentStore()
    faulty = FaultInjector(store, at=2, raises=ValueError)

    await faulty.proxy.save("S-1")
    with pytest.raises(ValueError):
        await faulty.proxy.load()

    assert faulty.awaited == ["save", "load"]


async def test_calls_after_the_fault_pass_through() -> None:
    store = _ShipmentStore()
    faulty = FaultInjector(store, at=1, raises=ValueError)

    with pytest.raises(ValueError):
        await faulty.proxy.load()
    assert await faulty.proxy.load() == "loaded"

    assert store.calls == ["load"]


async def test_cancelled_error_simulates_cancellation_at_the_await() -> None:
    faulty = FaultInjector(_ShipmentStore(), at=1, raises=asyncio.CancelledError)

    with pytest.raises(asyncio.CancelledError):
        await faulty.proxy.save("S-1")


async def test_exception_instance_is_raised_as_given() -> None:
    error = RuntimeError("synthetic outage")
    faulty = FaultInjector(_ShipmentStore(), at=1, raises=error)

    with pytest.raises(RuntimeError) as caught:
        await faulty.proxy.load()

    assert caught.value is error


async def test_fault_has_not_fired_before_the_nth_call() -> None:
    faulty = FaultInjector(_ShipmentStore(), at=3, raises=ValueError)

    await faulty.proxy.load()
    await faulty.proxy.load()

    assert not faulty.fired


async def test_sync_methods_and_attributes_pass_through_uncounted() -> None:
    store = _ShipmentStore()
    faulty = FaultInjector(store, at=1, raises=ValueError)

    assert faulty.proxy.label == "shipments"
    assert faulty.proxy.count() == 0
    assert faulty.awaited == []
    assert not faulty.fired


async def test_methods_limits_which_calls_count() -> None:
    store = _ShipmentStore()
    faulty = FaultInjector(store, at=1, raises=ValueError, methods={"save"})

    assert await faulty.proxy.load() == "loaded"
    with pytest.raises(ValueError):
        await faulty.proxy.save("S-1")

    assert faulty.awaited == ["save"]


async def test_after_lets_the_call_happen_then_raises() -> None:
    store = _ShipmentStore()
    faulty = FaultInjector(store, at=1, raises=asyncio.CancelledError, after=True)

    with pytest.raises(asyncio.CancelledError):
        await faulty.proxy.save("S-1")

    assert store.calls == ["save S-1"]


async def test_async_context_manager_calls_are_counted() -> None:
    session = _Session()
    faulty = FaultInjector(session, at=1, raises=ValueError)

    with pytest.raises(ValueError):
        async with faulty.proxy:
            pass

    assert not session.entered
    assert faulty.awaited == ["__aenter__"]


async def test_proxy_is_an_instance_of_the_target_class() -> None:
    faulty = FaultInjector(_ShipmentStore(), at=1, raises=ValueError)

    assert isinstance(faulty.proxy, _ShipmentStore)


async def test_attribute_writes_reach_the_target() -> None:
    store = _ShipmentStore()
    faulty = FaultInjector(store, at=1, raises=ValueError)

    faulty.proxy.label = "invoices"

    assert store.label == "invoices"


async def test_at_must_be_positive() -> None:
    with pytest.raises(ValueError):
        FaultInjector(_ShipmentStore(), at=0, raises=ValueError)


@_AENTER_ESCAPES
async def test_async_with_binds_the_proxy_when_aenter_returns_the_target() -> None:
    faulty = FaultInjector(_Session(), at=1, raises=ValueError, methods={"save"})

    async with faulty.proxy as bound:
        assert bound is faulty.proxy
        with pytest.raises(ValueError):
            await bound.save()

    assert faulty.awaited == ["save"]


async def test_async_with_binds_what_aenter_returns_otherwise() -> None:
    opener = _Opener()
    faulty = FaultInjector(opener, at=1, raises=ValueError, methods={"save"})

    async with faulty.proxy as bound:
        assert bound is opener.session


@_NO_ASYNC_ITERATION
async def test_async_iteration_counts_each_awaited_anext() -> None:
    faulty = FaultInjector(_Stream("P-1", "P-2", "P-3"), at=2, raises=ValueError)
    seen: list[str] = []

    with pytest.raises(ValueError):
        async for item in faulty.proxy:
            seen.append(item)

    assert seen == ["P-1"]
    assert faulty.awaited == ["__anext__", "__anext__"]


@_NO_ASYNC_ITERATION
async def test_async_iteration_counts_the_final_anext() -> None:
    faulty = FaultInjector(_Stream("P-1"), at=3, raises=ValueError)

    assert [item async for item in faulty.proxy] == ["P-1"]

    assert faulty.awaited == ["__anext__", "__anext__"]
    assert not faulty.fired


@_NO_ASYNC_ITERATION
async def test_async_iteration_counts_a_separate_iterator() -> None:
    faulty = FaultInjector(_Feed("P-1", "P-2"), at=2, raises=ValueError)
    seen: list[str] = []

    with pytest.raises(ValueError):
        async for item in faulty.proxy:
            seen.append(item)

    assert seen == ["P-1"]


@_NO_ASYNC_ITERATION
async def test_async_iteration_counts_an_async_generator_iterator() -> None:
    faulty = FaultInjector(_GeneratedFeed(), at=4, raises=ValueError)

    assert [item async for item in faulty.proxy] == ["P-1", "P-2"]

    assert faulty.awaited == ["__anext__", "__anext__", "__anext__"]


async def test_proxy_of_a_non_iterable_is_not_async_iterable() -> None:
    faulty = FaultInjector(_ShipmentStore(), at=1, raises=ValueError)

    assert not isinstance(faulty.proxy, AsyncIterable)
