"""Run the ``MessageBroker`` contract suite against ``InMemoryMessageBroker``."""

from __future__ import annotations

import pytest

from pydomain.testing import InMemoryMessageBroker
from pydomain.testing.contracts import MessageBrokerContract


class TestInMemoryMessageBrokerContract(MessageBrokerContract):
    @pytest.fixture
    def broker(self) -> InMemoryMessageBroker:
        return InMemoryMessageBroker()
