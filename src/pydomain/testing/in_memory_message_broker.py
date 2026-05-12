"""In-memory message broker for testing."""

from __future__ import annotations

from pydomain.cqrs.integration_events import IntegrationEvent


class InMemoryMessageBroker:
    """In-memory message broker for testing.

    Captures all published events in a list for test assertions.
    ``start()`` and ``stop()`` are no-ops.

    Parameters
    ----------
    published:
        List of ``(topic, event)`` tuples captured from ``publish()`` calls.
    """

    def __init__(self) -> None:
        self.published: list[tuple[str, IntegrationEvent]] = []

    async def publish(self, topic: str, event: IntegrationEvent) -> None:
        """Append ``(topic, event)`` to the published list.

        Parameters
        ----------
        topic:
            The topic the event was published to.
        event:
            The published integration event.
        """
        self.published.append((topic, event))

    async def start(self) -> None:
        """No-op. Included for protocol conformance."""

    async def stop(self) -> None:
        """No-op. Included for protocol conformance."""
