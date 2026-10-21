"""Round-2 probes for PR #112 (head 2686dbe): the ContextVar token scheme."""

from __future__ import annotations

import asyncio
import contextvars
import time
from collections.abc import AsyncIterator

import anyio
import pytest

from pydomain.testing import FakeLockProvider

pytestmark = pytest.mark.anyio


@pytest.fixture
async def held() -> AsyncIterator[FakeLockProvider]:
    locks = FakeLockProvider()
    await locks.acquire("stock:fx")  # acquired in the fixture
    yield locks


async def test_r1_fixture_acquire_test_release(held: FakeLockProvider) -> None:
    """A key acquired in an async fixture and released in the test body."""
    await held.release("stock:fx")
    with anyio.fail_after(0.5):
        await held.acquire("stock:fx")


@pytest.fixture
async def releasing_fixture() -> AsyncIterator[FakeLockProvider]:
    locks = FakeLockProvider()
    yield locks
    await locks.release("stock:td")  # teardown releases what the test took


async def test_r1b_test_acquire_fixture_teardown_release(
    releasing_fixture: FakeLockProvider,
) -> None:
    await releasing_fixture.acquire("stock:td")


async def test_r2_gather_acquire_then_release_in_parent() -> None:
    """Acquire several keys concurrently with gather, release in the parent."""
    locks = FakeLockProvider()
    await asyncio.gather(locks.acquire("a"), locks.acquire("b"))
    try:
        await locks.release("a")
        outcome = "released"
    except RuntimeError:
        outcome = "RuntimeError"
    print("R2 gather-acquire release:", outcome)
    assert outcome == "RuntimeError"  # documents: legitimate-looking release raises


async def test_r3_unrelated_task_release_raises_and_key_stays_held() -> None:
    locks = FakeLockProvider()
    await locks.acquire("k")

    async def other() -> None:
        await locks.release("k")

    t = asyncio.get_running_loop().create_task(other(), context=contextvars.Context())
    with pytest.raises(RuntimeError):
        await t
    # task started *after* acquire copies the context, so it CAN release:
    await asyncio.create_task(locks.release("k"))
    with anyio.fail_after(0.5):
        await locks.acquire("k")


async def test_r4_task_started_before_acquire_cannot_release() -> None:
    locks = FakeLockProvider()
    go = asyncio.Event()

    async def early() -> str:
        await go.wait()
        try:
            await locks.release("k")
            return "released"
        except RuntimeError:
            return "RuntimeError"

    t = asyncio.create_task(early())
    await asyncio.sleep(0)
    await locks.acquire("k")
    go.set()
    assert await t == "RuntimeError"
    await locks.release("k")


async def test_r5_double_release_same_context_is_silent() -> None:
    """Behaviour change: a double release used to raise RuntimeError
    ('Lock is not acquired.'); now the second call is a silent no-op."""
    locks = FakeLockProvider()
    await locks.acquire("k")
    await locks.release("k")
    await locks.release("k")  # no error


async def test_r6_child_started_while_holding_frees_parent_hold() -> None:
    """A child started while the parent holds can free the parent's live hold."""
    locks = FakeLockProvider()
    await locks.acquire("k")
    await asyncio.create_task(locks.release("k"))
    got = False

    async def contender() -> None:
        nonlocal got
        await locks.acquire("k")
        got = True
        await locks.release("k")

    await asyncio.create_task(contender())
    assert got
    await locks.release("k")  # parent's own release: stale -> silent no-op


async def test_r7_two_providers_same_key_are_independent() -> None:
    a, b = FakeLockProvider(), FakeLockProvider()
    await a.acquire("k")
    await b.acquire("k")
    await a.release("k")
    with anyio.fail_after(0.5):
        await a.acquire("k")  # a is free again; b's hold untouched
    await a.release("k")
    await b.release("k")


@pytest.mark.parametrize("n", [2000, 8000, 32000, 128000])
async def test_r8_tokens_accumulate_per_task(n: int) -> None:
    """One long-lived task, n distinct keys: each acquire copies the whole dict."""
    locks = FakeLockProvider()
    t0 = time.perf_counter()
    for i in range(n):
        await locks.acquire(f"order:{i}")
        await locks.release(f"order:{i}")
    dt = time.perf_counter() - t0
    print(f"R8 n={n}: {dt:.3f}s")


async def test_r9_contract_detects_naive_provider() -> None:
    """The how-to's old naive provider must fail the late-release contract."""
    from pydomain.testing.contracts.lock_provider import LockProviderContract

    class Naive:
        def __init__(self) -> None:
            self._locks: dict[str, asyncio.Lock] = {}

        async def acquire(self, key: str) -> None:
            await self._locks.setdefault(key, asyncio.Lock()).acquire()

        async def release(self, key: str) -> None:
            if key in self._locks and self._locks[key].locked():
                self._locks[key].release()

    case = LockProviderContract()
    with pytest.raises(AssertionError):
        await case.test_late_release_after_another_holder_took_the_key_does_not_free_it(
            Naive()  # type: ignore[arg-type]
        )
