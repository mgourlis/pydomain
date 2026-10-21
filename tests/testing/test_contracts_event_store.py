"""Run the ``EventStore`` contract suite against ``FakeEventStore``."""

from __future__ import annotations

import pytest

from pydomain.testing import FakeEventStore
from pydomain.testing.contracts import EventStoreContract


class TestFakeEventStoreContract(EventStoreContract):
    @pytest.fixture
    def event_store(self) -> FakeEventStore:
        return FakeEventStore()
