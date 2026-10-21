"""Run the ``EventStore`` contract suite against ``FakeEventStore``."""

from __future__ import annotations

import pytest

from pydomain.testing import FakeEventStore
from pydomain.testing.contracts import EventStoreContract

pytestmark = pytest.mark.xfail(
    strict=True,
    raises=NotImplementedError,
    reason="DCE-110 contract cases not implemented yet",
)


class TestFakeEventStoreContract(EventStoreContract):
    @pytest.fixture
    def event_store(self) -> FakeEventStore:
        return FakeEventStore()
