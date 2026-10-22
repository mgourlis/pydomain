"""Run the ``MessageReservationStore`` contract suite against its fake."""

from __future__ import annotations

import pytest

from pydomain.testing import FakeProcessedMessageStore
from pydomain.testing.contracts import MessageReservationStoreContract


class TestFakeMessageReservationStoreContract(MessageReservationStoreContract):
    @pytest.fixture
    def message_reservation_store(self) -> FakeProcessedMessageStore:
        return FakeProcessedMessageStore()
