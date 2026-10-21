"""Run the ``OutboxStore`` contract suite against ``FakeOutboxStore``."""

from __future__ import annotations

import pytest

from pydomain.testing import FakeOutboxStore
from pydomain.testing.contracts import OutboxStoreContract


class TestFakeOutboxStoreContract(OutboxStoreContract):
    @pytest.fixture
    def outbox_store(self) -> FakeOutboxStore:
        return FakeOutboxStore()
