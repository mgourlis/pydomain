"""Run the ``MessageBroker`` contract suite against ``InMemoryMessageBroker``."""

from __future__ import annotations

import pytest

from pydomain.testing import InMemoryMessageBroker
from pydomain.testing.contracts import MessageBrokerContract

pytestmark = pytest.mark.xfail(
    strict=True,
    raises=NotImplementedError,
    reason="DCE-110 contract cases not implemented yet",
)


class TestInMemoryMessageBrokerContract(MessageBrokerContract):
    @pytest.fixture
    def broker(self) -> InMemoryMessageBroker:
        return InMemoryMessageBroker()
