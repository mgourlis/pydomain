"""Run the ``MessageSubscriber`` contract suite against its in-memory fake."""

from __future__ import annotations

from typing import Any

import pytest

from pydomain.infrastructure.message_subscriber import MessageSubscriber
from pydomain.testing import InMemoryMessageSubscriber
from pydomain.testing.contracts import MessageSubscriberContract


class TestInMemoryMessageSubscriberContract(MessageSubscriberContract):
    @pytest.fixture
    def subscriber(self) -> InMemoryMessageSubscriber:
        return InMemoryMessageSubscriber()

    async def deliver(
        self, subscriber: MessageSubscriber, topic: str, payload: dict[str, Any]
    ) -> None:
        assert isinstance(subscriber, InMemoryMessageSubscriber)
        await subscriber.simulate_message(topic, payload)
