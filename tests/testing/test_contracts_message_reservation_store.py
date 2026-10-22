"""Run the ``MessageReservationStore`` contract suite against its fake."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from pydomain.testing import FakeProcessedMessageStore
from pydomain.testing.contracts import MessageReservationStoreContract


class TestFakeMessageReservationStoreContract(MessageReservationStoreContract):
    @pytest.fixture
    def message_reservation_store(self) -> FakeProcessedMessageStore:
        return FakeProcessedMessageStore()


class _FreesOnSet(FakeProcessedMessageStore):
    """A non-conforming store: ``set`` frees the reservation instead of
    completing it, so a later ``reserve`` hands out a new token (B-142)."""

    async def set(self, message_id: UUID, result: Any) -> None:
        await super().set(message_id, result)
        self._reservations.pop(message_id, None)


@pytest.mark.xfail(
    strict=True,
    raises=AttributeError,
    reason="DCE-146 B-142: the contract does not yet require a completed "
    "reservation to refuse reserve",
)
@pytest.mark.anyio
async def test_contract_refuses_a_store_whose_set_frees_the_reservation() -> None:
    """The contract case fails for a store that frees the id on ``set``."""
    case = MessageReservationStoreContract.test_completed_reservation_refuses_reserve

    with pytest.raises(AssertionError):
        await case(MessageReservationStoreContract(), _FreesOnSet())


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
