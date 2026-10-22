"""Contract suite for the ``MessageReservationStore`` Protocol (ADR-079 point 4)."""

from __future__ import annotations

from collections.abc import Hashable
from typing import Protocol
from uuid import UUID, uuid4

import anyio
import pytest

from pydomain.cqrs.idempotency import (
    MISSING,
    MessageReservationStore,
    ProcessedMessageStore,
)
from pydomain.testing.contracts._backend import AsyncioBackend

__all__ = ["MessageReservationStoreContract"]


class _ReservingStore(ProcessedMessageStore, MessageReservationStore, Protocol):
    """A store that implements both Protocols, as ``IdempotencyBehavior`` uses it."""


class MessageReservationStoreContract(AsyncioBackend):
    """Cases every :class:`~pydomain.cqrs.idempotency.MessageReservationStore` passes.

    The store under test implements
    :class:`~pydomain.cqrs.idempotency.ProcessedMessageStore` too, since its
    ``set`` completes a reservation. Override the ``message_reservation_store``
    fixture to return a fresh, empty store for each test. The cases do not
    wait for a TTL: give the store one long enough that no reservation
    expires while a case runs.
    """

    @pytest.fixture
    def message_reservation_store(self) -> _ReservingStore:
        """Return a fresh, empty store. Override in your subclass."""
        raise NotImplementedError(
            "Override the `message_reservation_store` fixture to return your adapter."
        )

    @pytest.mark.anyio
    async def test_satisfies_protocol(
        self, message_reservation_store: _ReservingStore
    ) -> None:
        """The store is a ``MessageReservationStore`` and a processed-message store."""
        assert isinstance(message_reservation_store, MessageReservationStore)
        assert isinstance(message_reservation_store, ProcessedMessageStore)

    @pytest.mark.anyio
    async def test_reserve_returns_a_token_the_first_time(
        self, message_reservation_store: _ReservingStore
    ) -> None:
        """The first ``reserve`` of an id returns a token, not ``None``."""
        assert await message_reservation_store.reserve(uuid4()) is not None

    @pytest.mark.anyio
    async def test_reserve_returns_none_while_reserved(
        self, message_reservation_store: _ReservingStore
    ) -> None:
        """A second ``reserve`` of a reserved id returns ``None``."""
        message_id = uuid4()

        await message_reservation_store.reserve(message_id)

        assert await message_reservation_store.reserve(message_id) is None

    @pytest.mark.anyio
    async def test_release_with_the_token_allows_a_new_reservation(
        self, message_reservation_store: _ReservingStore
    ) -> None:
        """``release`` with the reservation's token frees the id."""
        message_id = uuid4()
        token = await message_reservation_store.reserve(message_id)
        assert token is not None

        await message_reservation_store.release(message_id, token)

        assert await message_reservation_store.reserve(message_id) is not None

    @pytest.mark.anyio
    async def test_stale_token_does_not_release_a_newer_reservation(
        self, message_reservation_store: _ReservingStore
    ) -> None:
        """A token whose reservation ended frees nothing (B-120)."""
        message_id = uuid4()
        stale = await message_reservation_store.reserve(message_id)
        assert stale is not None
        await message_reservation_store.release(message_id, stale)
        newer = await message_reservation_store.reserve(message_id)
        assert newer is not None

        await message_reservation_store.release(message_id, stale)

        assert newer != stale
        assert await message_reservation_store.reserve(message_id) is None

    @pytest.mark.anyio
    async def test_concurrent_reserve_admits_one_caller(
        self, message_reservation_store: _ReservingStore
    ) -> None:
        """Of concurrent ``reserve`` calls for one id, exactly one gets a token."""
        message_id = uuid4()
        tokens: list[Hashable | None] = []

        async def reserve(id_: UUID) -> None:
            tokens.append(await message_reservation_store.reserve(id_))

        with anyio.fail_after(10):
            async with anyio.create_task_group() as tg:
                for _ in range(10):
                    tg.start_soon(reserve, message_id)

        assert len(tokens) == 10
        assert sum(token is not None for token in tokens) == 1

    @pytest.mark.anyio
    async def test_reserved_id_is_not_a_result(
        self, message_reservation_store: _ReservingStore
    ) -> None:
        """``get`` returns ``MISSING`` for an id that is only reserved."""
        message_id = uuid4()

        await message_reservation_store.reserve(message_id)

        assert await message_reservation_store.get(message_id) is MISSING

    @pytest.mark.anyio
    async def test_set_completes_a_reservation(
        self, message_reservation_store: _ReservingStore
    ) -> None:
        """``set`` completes the reservation: ``get`` returns the result, and a
        release with the reservation's token afterwards does not drop it."""
        message_id = uuid4()
        result = {"order_id": "order-1", "status": "placed"}
        token = await message_reservation_store.reserve(message_id)
        assert token is not None

        await message_reservation_store.set(message_id, result)
        await message_reservation_store.release(message_id, token)

        assert await message_reservation_store.get(message_id) == result

    @pytest.mark.anyio
    async def test_completed_reservation_refuses_reserve(
        self, message_reservation_store: _ReservingStore
    ) -> None:
        """Once ``set`` completed a reservation, ``reserve`` returns ``None``,
        also after a later ``release`` with the old token (B-142)."""
        message_id = uuid4()
        token = await message_reservation_store.reserve(message_id)
        assert token is not None

        await message_reservation_store.set(message_id, {"status": "placed"})
        completed = await message_reservation_store.reserve(message_id)
        await message_reservation_store.release(message_id, token)
        released = await message_reservation_store.reserve(message_id)

        assert completed is None
        assert released is None

    @pytest.mark.anyio
    async def test_reservations_do_not_collide_with_check_and_set(
        self, message_reservation_store: _ReservingStore
    ) -> None:
        """Reserving an id never marks it processed, and ``check_and_set``
        never reserves it."""
        marked, reserved = uuid4(), uuid4()

        await message_reservation_store.check_and_set(marked)
        await message_reservation_store.reserve(reserved)

        assert await message_reservation_store.reserve(marked) is not None
        assert await message_reservation_store.check_and_set(reserved) is False
