"""In-memory fake of ``ProcessedMessageStore`` and ``MessageReservationStore``."""

from __future__ import annotations

import time
from collections.abc import Hashable
from typing import Any
from uuid import UUID, uuid4

from pydomain.cqrs.idempotency import (
    MISSING,
    MessageReservationStore,
    ProcessedMessageStore,
)

_COMPLETED: Any = object()
"""The token a reservation holds once ``set`` completed it; no caller has it."""


class FakeProcessedMessageStore(ProcessedMessageStore, MessageReservationStore):
    """In-memory fake of :class:`ProcessedMessageStore` and
    :class:`~pydomain.cqrs.idempotency.MessageReservationStore`.

    Uses plain ``dict`` objects keyed by message UUID. Safe for
    single-threaded async test scenarios — not suitable for multi-threaded or
    multi-process tests.

    Results (``set``, ``get``), the marks of ``check_and_set`` and the
    reservations each have their own storage, so a mark is not a result
    (``get`` stays ``MISSING``), ``check_and_set`` reads marks only, and
    reserving an id never marks it processed. ``contains`` covers results
    and marks. ``reserve`` returns a new token (a ``UUID``), or ``None``
    while the id is reserved; ``release(message_id, token)`` frees the id
    only if *token* still holds it. ``set`` completes a reservation: the id
    then stays reserved for good, so ``IdempotencyBehavior`` finds the result
    when it was missed by its first ``get``.

    Parameters
    ----------
    reservation_ttl:
        Seconds after which a reservation that was neither released nor
        completed expires, as a real store's TTL frees the reservation of a
        crashed process. ``None`` (the default): reservations never expire.
    """

    def __init__(self, *, reservation_ttl: float | None = None) -> None:
        self._store: dict[UUID, Any] = {}
        self._marks: set[UUID] = set()
        self._reservations: dict[UUID, tuple[Hashable, float | None]] = {}
        self._reservation_ttl = reservation_ttl

    async def get(self, message_id: UUID) -> Any:
        """Return the cached result, or ``MISSING`` if not found."""
        return self._store.get(message_id, MISSING)

    async def set(self, message_id: UUID, result: Any) -> None:
        """Store *result* keyed by *message_id*; complete its reservation."""
        self._store[message_id] = result
        if message_id in self._reservations:
            self._reservations[message_id] = (_COMPLETED, None)

    async def contains(self, message_id: UUID) -> bool:
        """Return ``True`` if *message_id* has a result or a mark."""
        return message_id in self._store or message_id in self._marks

    async def check_and_set(self, message_id: UUID) -> bool:
        """Atomically check and mark *message_id*.

        Reads the marks only: a result stored by ``set`` is not a mark, and
        the mark creates no result. Returns ``True`` if already marked,
        ``False`` if this is the first time.
        """
        if message_id in self._marks:
            return True
        self._marks.add(message_id)
        return False

    async def reserve(self, message_id: UUID) -> Hashable | None:
        """Reserve *message_id* and return a new token, or ``None`` if reserved."""
        held = self._reservations.get(message_id)
        if held is not None and not _expired(held):
            return None
        token = uuid4()
        expires = (
            None
            if self._reservation_ttl is None
            else time.monotonic() + self._reservation_ttl
        )
        self._reservations[message_id] = (token, expires)
        return token

    async def release(self, message_id: UUID, token: Hashable) -> None:
        """Free *message_id* if *token* still holds its reservation."""
        held = self._reservations.get(message_id)
        if held is not None and held[0] == token:
            del self._reservations[message_id]


def _expired(held: tuple[Hashable, float | None]) -> bool:
    """Whether the reservation *held* passed its expiry time."""
    expires = held[1]
    return expires is not None and time.monotonic() >= expires
