"""Tests for the locking infrastructure (FakeLockProvider, protocols)."""

from __future__ import annotations

from dataclasses import dataclass

import anyio
import pytest

from pydomain.cqrs.locking import (
    DictLockKeyResolver,
    LockKeyResolver,
    LockProvider,
)
from pydomain.testing import FakeLockProvider

# ── Test message types (simple dataclasses, not full Command subclasses) ──


@dataclass
class _SampleMessage:
    id: str
    data: str = ""


@dataclass
class _AnotherMessage:
    entity_id: str
    owner_id: str


# ── Protocol checks (runtime_checkable) ─────────────────────────────────


class TestLockProtocols:
    """Verify that both protocols are runtime-checkable."""

    def test_lock_provider_isinstance_check_passes(self) -> None:
        assert isinstance(FakeLockProvider(), LockProvider)

    def test_lock_key_resolver_isinstance_check_passes(self) -> None:
        assert isinstance(DictLockKeyResolver(), LockKeyResolver)

    def test_unrelated_object_is_not_lock_provider(self) -> None:
        assert not isinstance("string", LockProvider)

    def test_unrelated_object_is_not_lock_key_resolver(self) -> None:
        assert not isinstance(42, LockKeyResolver)


# ── DictLockKeyResolver ────────────────────────────────────────────────


class TestDictLockKeyResolver:
    """Registry-based lock key resolver tests."""

    def test_resolve_returns_keys_from_single_registered_function(self) -> None:
        resolver = DictLockKeyResolver()
        resolver.register(_SampleMessage, lambda msg: [f"entity:{msg.id}"])

        keys = resolver.resolve(_SampleMessage(id="abc-123"))

        assert keys == ["entity:abc-123"]

    def test_resolve_aggregates_keys_from_multiple_registered_functions(
        self,
    ) -> None:
        resolver = DictLockKeyResolver()
        resolver.register(_SampleMessage, lambda msg: [f"entity:{msg.id}"])
        resolver.register(
            _SampleMessage,
            lambda msg: [f"tenant:{msg.data}", "global:lock"],
        )

        msg = _SampleMessage(id="abc-123", data="tenant-x")
        keys = resolver.resolve(msg)

        assert keys == ["entity:abc-123", "tenant:tenant-x", "global:lock"]

    def test_resolve_returns_empty_list_for_unregistered_message_type(
        self,
    ) -> None:
        resolver = DictLockKeyResolver()

        keys = resolver.resolve(_SampleMessage(id="x"))

        assert keys == []

    def test_registration_is_isolated_per_message_type(self) -> None:
        resolver = DictLockKeyResolver()
        resolver.register(_SampleMessage, lambda msg: [f"sample:{msg.id}"])

        keys = resolver.resolve(
            _AnotherMessage(entity_id="e1", owner_id="o1"),
        )

        assert keys == []

    def test_key_function_receives_the_actual_message_instance(self) -> None:
        resolver = DictLockKeyResolver()
        captured: list[_SampleMessage] = []

        def capture_key_fn(msg: _SampleMessage) -> list[str]:
            captured.append(msg)
            return [f"id:{msg.id}"]

        resolver.register(_SampleMessage, capture_key_fn)

        msg = _SampleMessage(id="test-msg")
        resolver.resolve(msg)

        assert len(captured) == 1
        # Identity check -- must be the exact object, not a copy
        assert captured[0] is msg


# ── FakeLockProvider ──────────────────────────────────────────────────


class TestFakeLockProvider:
    """Process-local lock provider backed by asyncio.Lock per key."""

    @pytest.mark.anyio
    async def test_acquire_creates_lock_and_acquires(self) -> None:
        provider = FakeLockProvider()

        await provider.acquire("key1")

        # The lock should now be created and held by this task
        assert "key1" in provider._locks
        assert provider._locks["key1"].locked()

        await provider.release("key1")
        assert not provider._locks["key1"].locked()

    @pytest.mark.anyio
    async def test_acquire_same_key_blocks_until_released(self) -> None:
        """Two workers sharing the same key execute their critical sections
        sequentially, never concurrently."""
        provider = FakeLockProvider()
        execution_order: list[int] = []

        async def worker(iterations: int) -> None:
            for _ in range(iterations):
                await provider.acquire("shared")
                # Critical section: read current length, yield, then append.
                # Without proper locking both workers could read the same
                # length and produce duplicate (or out-of-order) entries.
                idx = len(execution_order)
                await anyio.sleep(0.02)
                execution_order.append(idx)
                await provider.release("shared")

        async with anyio.create_task_group() as tg:
            tg.start_soon(worker, 3)
            tg.start_soon(worker, 3)

        assert execution_order == list(range(6))

    @pytest.mark.anyio
    async def test_release_frees_lock_for_subsequent_acquire(self) -> None:
        """After release, the same key can be acquired again."""
        provider = FakeLockProvider()

        await provider.acquire("key1")
        assert provider._locks["key1"].locked()

        await provider.release("key1")
        assert not provider._locks["key1"].locked()

        # Re-acquire should succeed immediately
        await provider.acquire("key1")
        assert provider._locks["key1"].locked()
        await provider.release("key1")

    @pytest.mark.anyio
    async def test_release_on_unknown_key_raises_key_error(self) -> None:
        provider = FakeLockProvider()

        with pytest.raises(KeyError, match="No lock registered for key: unknown"):
            await provider.release("unknown")

    @pytest.mark.anyio
    async def test_multiple_keys_can_be_acquired_without_blocking(
        self,
    ) -> None:
        """Different keys must not block each other."""
        provider = FakeLockProvider()
        state_a: list[int] = []
        state_b: list[int] = []

        async def worker(key: str, state: list[int]) -> None:
            for _ in range(3):
                await provider.acquire(key)
                state.append(len(state))
                await anyio.sleep(0.02)
                await provider.release(key)

        async with anyio.create_task_group() as tg:
            tg.start_soon(worker, "key-a", state_a)
            tg.start_soon(worker, "key-b", state_b)

        # Both workers should complete all iterations because they use
        # different keys and never contend.
        assert state_a == [0, 1, 2]
        assert state_b == [0, 1, 2]
