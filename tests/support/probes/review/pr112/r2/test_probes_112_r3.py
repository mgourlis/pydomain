"""Round-3 probes for PR #112 (head 5cc3bf1)."""

from __future__ import annotations

import asyncio

import pytest

from pydomain.testing import FakeLockProvider
from pydomain.testing import fake_lock_provider as F

pytestmark = pytest.mark.anyio


async def test_s1_second_release_same_context_raises() -> None:
    locks = FakeLockProvider()
    await locks.acquire("k")
    await locks.release("k")
    with pytest.raises(RuntimeError) as e:
        await locks.release("k")
    assert "k" not in str(e.value).split(";")[0].split()  # key-free message


async def test_s2_token_map_bounded_over_many_keys() -> None:
    locks = FakeLockProvider()
    for i in range(5000):
        await locks.acquire(f"order:{i}")
        await locks.release(f"order:{i}")
    assert len(F._TOKENS.get()) == 0


async def test_s3_child_release_leaves_one_stale_entry_in_parent() -> None:
    locks = FakeLockProvider()
    for i in range(100):
        await locks.acquire(f"k{i}")
        await asyncio.create_task(locks.release(f"k{i}"))
    print("S3 stale parent entries:", len(F._TOKENS.get()))
    assert len(F._TOKENS.get()) == 100  # bounded by such holds, not by all keys


async def test_s4_parent_release_after_child_freed_is_stale_noop() -> None:
    locks = FakeLockProvider()
    await locks.acquire("k")
    await asyncio.create_task(locks.release("k"))
    await locks.release("k")  # stale token: no-op, no raise
    await locks.acquire("k")
    await locks.release("k")
