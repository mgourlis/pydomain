"""Message broker protocol and in-memory implementation.

The ``MessageBroker`` protocol defines the contract for publishing integration
events to external message brokers (RabbitMQ, Kafka, etc.). The
``InMemoryMessageBroker`` provides a test double that captures published
events for test assertions.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from pydomain.cqrs.integration_events import IntegrationEvent

logger = logging.getLogger("pydomain.message_broker")


@runtime_checkable
class MessageBroker(Protocol):
    """Protocol for publishing integration events to external brokers.

    Implementations wrap production brokers (RabbitMQ, Kafka) for real
    event publishing. The protocol is runtime-checkable so that tests
    can use ``isinstance()`` checks.
    """

    async def publish(self, topic: str, event: IntegrationEvent) -> None:
        """Publish an integration event to the given topic.

        Parameters
        ----------
        topic:
            The topic or routing key to publish to.
        event:
            The integration event to publish.
        """

    async def start(self) -> None:
        """Initialize connection or resources.

        Called at application startup.
        """

    async def stop(self) -> None:
        """Graceful shutdown and resource cleanup.

        Called at application shutdown.
        """
