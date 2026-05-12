from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from pydomain.cqrs.idempotency import MISSING
from pydomain.testing import FakeProcessedCommandStore


class TestFakeProcessedCommandStore:
    """Tests for ``FakeProcessedCommandStore`` in-memory fake.

    Covers the ``get``, ``set``, and ``contains`` async methods, including
    overwrite semantics and instance isolation.
    """

    # ── get() ──────────────────────────────────────────────────────────────

    @pytest.mark.anyio
    async def test_get_returns_stored_result(self) -> None:
        """get() returns the value previously stored via set()."""
        store = FakeProcessedCommandStore()
        command_id = uuid4()
        expected: Any = {"status": "ok"}

        await store.set(command_id, expected)
        result = await store.get(command_id)

        assert result is expected

    @pytest.mark.anyio
    async def test_get_returns_missing_for_unknown_id(self) -> None:
        """get() returns MISSING sentinel when command_id was never stored."""
        store = FakeProcessedCommandStore()
        command_id = uuid4()

        result = await store.get(command_id)

        assert result is MISSING

    @pytest.mark.anyio
    async def test_get_returns_missing_after_clear(self) -> None:
        """get() returns MISSING for a command_id that was never set,
        even when other IDs exist in the store."""
        store = FakeProcessedCommandStore()
        stored_id = uuid4()
        missing_id = uuid4()

        await store.set(stored_id, "result")
        result = await store.get(missing_id)

        assert result is MISSING

    # ── set() ──────────────────────────────────────────────────────────────

    @pytest.mark.anyio
    async def test_set_stores_and_overwrites(self) -> None:
        """set() stores a result, and a subsequent set() overwrites it."""
        store = FakeProcessedCommandStore()
        command_id = uuid4()

        await store.set(command_id, "first")
        await store.set(command_id, "second")
        result = await store.get(command_id)

        assert result == "second"

    @pytest.mark.anyio
    async def test_set_multiple_ids_stored_independently(self) -> None:
        """set() stores results for distinct IDs independently."""
        store = FakeProcessedCommandStore()
        id_a = uuid4()
        id_b = uuid4()

        await store.set(id_a, "result-a")
        await store.set(id_b, "result-b")

        assert await store.get(id_a) == "result-a"
        assert await store.get(id_b) == "result-b"

    @pytest.mark.anyio
    async def test_set_with_none_result(self) -> None:
        """set() can store None as a valid result."""
        store = FakeProcessedCommandStore()
        command_id = uuid4()

        await store.set(command_id, None)
        result = await store.get(command_id)

        assert result is None

    # ── contains() ─────────────────────────────────────────────────────────

    @pytest.mark.anyio
    async def test_contains_returns_true_after_set(self) -> None:
        """contains() returns True after set() was called."""
        store = FakeProcessedCommandStore()
        command_id = uuid4()

        await store.set(command_id, "result")
        result = await store.contains(command_id)

        assert result is True

    @pytest.mark.anyio
    async def test_contains_returns_false_for_unknown_id(self) -> None:
        """contains() returns False for an ID that was never stored."""
        store = FakeProcessedCommandStore()
        command_id = uuid4()

        result = await store.contains(command_id)

        assert result is False

    @pytest.mark.anyio
    async def test_contains_returns_false_after_single_id_set_in_different_store(
        self,
    ) -> None:
        """contains() correctly distinguishes between IDs within one store."""
        store = FakeProcessedCommandStore()
        stored_id = uuid4()
        other_id = uuid4()

        await store.set(stored_id, "result")

        assert await store.contains(stored_id) is True
        assert await store.contains(other_id) is False

    # ── Instance isolation ─────────────────────────────────────────────────

    @pytest.mark.anyio
    async def test_store_is_isolated_per_instance(self) -> None:
        """Two separate FakeProcessedCommandStore instances don't share state."""
        store_a = FakeProcessedCommandStore()
        store_b = FakeProcessedCommandStore()
        command_id = uuid4()

        await store_a.set(command_id, "result-a")

        assert await store_a.contains(command_id) is True
        assert await store_a.get(command_id) == "result-a"
        assert await store_b.contains(command_id) is False
        assert await store_b.get(command_id) is MISSING

    @pytest.mark.anyio
    async def test_empty_store_returns_missing_and_false_for_contains(
        self,
    ) -> None:
        """A freshly created store returns MISSING for get and False for
        contains for any ID."""
        store = FakeProcessedCommandStore()
        command_id = uuid4()

        assert await store.get(command_id) is MISSING
        assert await store.contains(command_id) is False
