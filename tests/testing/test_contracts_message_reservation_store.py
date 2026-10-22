"""Run the ``MessageReservationStore`` contract suite against its fake."""

from __future__ import annotations

import pytest

from pydomain.testing import FakeProcessedMessageStore
from pydomain.testing.contracts.message_reservation_store import (
    MessageReservationStoreContract,
)

pytestmark = pytest.mark.xfail(
    strict=True,
    raises=(AttributeError, AssertionError, ExceptionGroup),
    reason="DCE-146 CQRS-04: FakeProcessedMessageStore cannot reserve a command",
)


class TestFakeMessageReservationStoreContract(MessageReservationStoreContract):
    @pytest.fixture
    def message_reservation_store(self) -> FakeProcessedMessageStore:
        return FakeProcessedMessageStore()
