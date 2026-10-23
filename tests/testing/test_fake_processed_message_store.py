from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from pydomain.cqrs.idempotency import MISSING
from pydomain.testing import FakeProcessedMessageStore


class TestFakeProcessedMessageStore:
    """Tests for ``FakeProcessedMessageStore`` in-memory fake.

    Covers the ``get``, ``set``, ``contains``, and ``check_and_set`` async
    methods, including overwrite semantics and instance isolation.
    """

    # ── get() ──────────────────────────────────────────────────────────────

    @pytest.mark.anyio
    async def test_get_returns_stored_result(self) -> None:
        """get() returns the value previously stored via set()."""
        store = FakeProcessedMessageStore()
        message_id = uuid4()
        expected: Any = {"status": "ok"}

        await store.set(message_id, expected)
        result = await store.get(message_id)

        assert result is expected

    @pytest.mark.anyio
    async def test_get_returns_missing_for_unknown_id(self) -> None:
        """get() returns MISSING sentinel when message_id was never stored."""
        store = FakeProcessedMessageStore()
        message_id = uuid4()

        result = await store.get(message_id)

        assert result is MISSING

    @pytest.mark.anyio
    async def test_get_returns_missing_for_unset_id(self) -> None:
        """get() returns MISSING for a message_id that was never set,
        even when other IDs exist in the store."""
        store = FakeProcessedMessageStore()
        stored_id = uuid4()
        missing_id = uuid4()

        await store.set(stored_id, "result")
        result = await store.get(missing_id)

        assert result is MISSING

    # ── set() ──────────────────────────────────────────────────────────────

    @pytest.mark.anyio
    async def test_set_stores_and_overwrites(self) -> None:
        """set() stores a result, and a subsequent set() overwrites it."""
        store = FakeProcessedMessageStore()
        message_id = uuid4()

        await store.set(message_id, "first")
        await store.set(message_id, "second")
        result = await store.get(message_id)

        assert result == "second"

    @pytest.mark.anyio
    async def test_set_multiple_ids_stored_independently(self) -> None:
        """set() stores results for distinct IDs independently."""
        store = FakeProcessedMessageStore()
        id_a = uuid4()
        id_b = uuid4()

        await store.set(id_a, "result-a")
        await store.set(id_b, "result-b")

        assert await store.get(id_a) == "result-a"
        assert await store.get(id_b) == "result-b"

    @pytest.mark.anyio
    async def test_set_with_none_result(self) -> None:
        """set() can store None as a valid result."""
        store = FakeProcessedMessageStore()
        message_id = uuid4()

        await store.set(message_id, None)
        result = await store.get(message_id)

        assert result is None

    # ── contains() ─────────────────────────────────────────────────────────

    @pytest.mark.anyio
    async def test_contains_returns_true_after_set(self) -> None:
        """contains() returns True after set() was called."""
        store = FakeProcessedMessageStore()
        message_id = uuid4()

        await store.set(message_id, "result")
        result = await store.contains(message_id)

        assert result is True

    @pytest.mark.anyio
    async def test_contains_returns_false_for_unknown_id(self) -> None:
        """contains() returns False for an ID that was never stored."""
        store = FakeProcessedMessageStore()
        message_id = uuid4()

        result = await store.contains(message_id)

        assert result is False

    @pytest.mark.anyio
    async def test_contains_distinguishes_ids(self) -> None:
        """contains() correctly distinguishes between IDs within one store."""
        store = FakeProcessedMessageStore()
        stored_id = uuid4()
        other_id = uuid4()

        await store.set(stored_id, "result")

        assert await store.contains(stored_id) is True
        assert await store.contains(other_id) is False

    # ── check_and_set() ────────────────────────────────────────────────────

    @pytest.mark.anyio
    async def test_check_and_set_returns_false_on_first_call(self) -> None:
        """check_and_set() returns False the first time an ID is seen."""
        store = FakeProcessedMessageStore()
        message_id = uuid4()

        result = await store.check_and_set(message_id)

        assert result is False

    @pytest.mark.anyio
    async def test_check_and_set_returns_true_on_second_call(self) -> None:
        """check_and_set() returns True for an ID that was already seen."""
        store = FakeProcessedMessageStore()
        message_id = uuid4()

        await store.check_and_set(message_id)
        result = await store.check_and_set(message_id)

        assert result is True

    @pytest.mark.anyio
    async def test_check_and_set_marks_contains(self) -> None:
        """After check_and_set(), contains() returns True."""
        store = FakeProcessedMessageStore()
        message_id = uuid4()

        await store.check_and_set(message_id)

        assert await store.contains(message_id) is True

    @pytest.mark.anyio
    async def test_check_and_set_does_not_affect_other_ids(self) -> None:
        """check_and_set() for one ID does not affect other IDs."""
        store = FakeProcessedMessageStore()
        id_a = uuid4()
        id_b = uuid4()

        await store.check_and_set(id_a)

        assert await store.check_and_set(id_b) is False
        assert await store.contains(id_a) is True
        assert await store.contains(id_b) is True  # now marked by check_and_set

    @pytest.mark.anyio
    async def test_check_and_set_ignores_results(self) -> None:
        """check_and_set() reads marks only: a result set() stored is no mark.

        Replaces ``test_check_and_set_after_set_returns_true`` (B-151).
        """
        store = FakeProcessedMessageStore()
        message_id = uuid4()

        await store.set(message_id, "some-result")
        first = await store.check_and_set(message_id)
        second = await store.check_and_set(message_id)

        assert first is False
        assert second is True
        assert await store.get(message_id) == "some-result"

    # ── Instance isolation ─────────────────────────────────────────────────

    @pytest.mark.anyio
    async def test_store_is_isolated_per_instance(self) -> None:
        """Two separate FakeProcessedMessageStore instances don't share state."""
        store_a = FakeProcessedMessageStore()
        store_b = FakeProcessedMessageStore()
        message_id = uuid4()

        await store_a.set(message_id, "result-a")

        assert await store_a.contains(message_id) is True
        assert await store_a.get(message_id) == "result-a"
        assert await store_b.contains(message_id) is False
        assert await store_b.get(message_id) is MISSING

    @pytest.mark.anyio
    async def test_empty_store_returns_missing_and_false(self) -> None:
        """A freshly created store returns MISSING for get and False for
        contains for any ID."""
        store = FakeProcessedMessageStore()
        message_id = uuid4()

        assert await store.get(message_id) is MISSING
        assert await store.contains(message_id) is False


class TestFakeReservations:
    """``reserve`` and ``release`` of ``FakeProcessedMessageStore`` (ADR-079 point 4).

    The cases every reserving store passes are in
    ``MessageReservationStoreContract``; these pin what is particular to the
    fake: its optional TTL and its completed reservations.
    """

    _TTL = 0.02

    @pytest.mark.anyio
    async def test_reservation_without_a_ttl_does_not_expire(self) -> None:
        store = FakeProcessedMessageStore()
        message_id = uuid4()
        await store.reserve(message_id)

        await asyncio.sleep(self._TTL * 2)

        assert await store.reserve(message_id) is None

    @pytest.mark.anyio
    async def test_reservation_expires_after_the_ttl(self) -> None:
        store = FakeProcessedMessageStore(reservation_ttl=self._TTL)
        message_id = uuid4()
        await store.reserve(message_id)
        assert await store.reserve(message_id) is None

        await asyncio.sleep(self._TTL * 2)

        assert await store.reserve(message_id) is not None

    @pytest.mark.anyio
    async def test_completed_reservation_stays_reserved(self) -> None:
        """After ``set``, the id is not reserved again, even past the TTL: a
        dispatch that missed the result in its first ``get`` finds it after
        ``reserve`` returns ``None``."""
        store = FakeProcessedMessageStore(reservation_ttl=self._TTL)
        message_id = uuid4()
        token = await store.reserve(message_id)
        assert token is not None
        await store.set(message_id, "result")

        await asyncio.sleep(self._TTL * 2)

        assert await store.reserve(message_id) is None
        assert await store.get(message_id) == "result"

    @pytest.mark.anyio
    async def test_release_of_an_unreserved_id_does_nothing(self) -> None:
        store = FakeProcessedMessageStore()
        message_id = uuid4()

        await store.release(message_id, "a-token-never-issued")

        assert await store.reserve(message_id) is not None

    @pytest.mark.anyio
    async def test_reservations_are_isolated_per_instance(self) -> None:
        store_a = FakeProcessedMessageStore()
        store_b = FakeProcessedMessageStore()
        message_id = uuid4()

        await store_a.reserve(message_id)

        assert await store_b.reserve(message_id) is not None
        assert await store_a.contains(message_id) is False
