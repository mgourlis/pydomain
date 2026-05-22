"""Message subscriber protocol and inbound event gateway.

The ``MessageSubscriber`` protocol defines the contract for receiving
integration events from external brokers (Kafka, RabbitMQ). The
``InboundEventGateway`` bridges the subscriber side by hydrating raw
JSON payloads into typed ``IntegrationEvent`` instances, translating them
to ``DomainEvent`` instances, and dispatching them into the internal
``MessageBus``.

Message Acknowledgment
----------------------
The gateway distinguishes two failure modes:

- **Validation/translation failures** (bad payload, unknown topic):
  logged and swallowed. The exception does **not** propagate, so the
  concrete subscriber implementation should ACK the message (poison
  message — it will never succeed).

- **Dispatch failures** (domain event handler error): the exception
  **propagates** so the subscriber can NACK the message, triggering a
  retry. The concrete subscriber implementation owns the ACK/NACK
  decision for this case.

Example
-------
Bootstrap the gateway for an e-commerce context::

    from uuid import UUID
    from pydomain.infrastructure.message_subscriber import (
        InboundEventGateway,
        MessageSubscriber,
    )
    from pydomain.infrastructure.message_bus import MessageBus
    from pydomain.cqrs.integration_events import IntegrationEvent
    from pydomain.ddd.domain_event import DomainEvent


    class ShipmentFailedIntegrationEvent(IntegrationEvent):
        order_id: str
        failure_reason: str


    class ExternalShipmentFailed(DomainEvent):
        order_id: UUID
        reason: str


    def translate_shipment_failed(
        event: ShipmentFailedIntegrationEvent,
    ) -> ExternalShipmentFailed:
        return ExternalShipmentFailed(
            order_id=UUID(event.order_id),
            reason=event.failure_reason,
        )


    subscriber: MessageSubscriber = KafkaMessageSubscriber(...)  # concrete impl
    bus = MessageBus()
    gateway = InboundEventGateway(subscriber, bus)
    gateway.register_translation(
        "shipping.shipment.failed",
        ShipmentFailedIntegrationEvent,
        translate_shipment_failed,
    )
    await subscriber.start()
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from pydantic import ValidationError

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.message_bus import MessageBus

logger = logging.getLogger("pydomain.message_subscriber")


@runtime_checkable
class MessageSubscriber(Protocol):
    """Protocol for subscribing to messages from external brokers.

    Implementations wrap production brokers (Kafka, RabbitMQ) to receive
    integration events. This is the subscriber-side counterpart to
    ``MessageBroker``.

    The subscriber delivers raw JSON dict payloads. Type resolution and
    hydration is handled by the ``InboundEventGateway``.
    """

    def subscribe(
        self, topic: str, handler: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None:
        """Register a handler for messages on the given topic.

        Parameters
        ----------
        topic:
            The topic or routing key to subscribe to.
        handler:
            An async callable that receives the raw JSON dict payload.
        """

    async def start(self) -> None:
        """Start consuming messages.

        Called at application startup.
        """

    async def stop(self) -> None:
        """Graceful shutdown and resource cleanup.

        Called at application shutdown.
        """


class InboundEventGateway:
    """Bridges external message brokers to the internal domain message bus.

    Receives raw JSON payloads from a ``MessageSubscriber``, hydrates them
    into typed ``IntegrationEvent`` instances (flat payload pattern — the
    topic implies the type, not an envelope), translates them to
    ``DomainEvent`` instances via an Anti-Corruption Layer translator, and
    dispatches them into the ``MessageBus`` for internal routing.

    Parameters
    ----------
    subscriber:
        The ``MessageSubscriber`` that receives messages from the external
        broker.
    message_bus:
        The internal ``MessageBus`` to dispatch translated domain events
        into.
    """

    def __init__(self, subscriber: MessageSubscriber, message_bus: MessageBus) -> None:
        self._subscriber = subscriber
        self._message_bus = message_bus
        self._registry: dict[
            str,
            tuple[
                type[IntegrationEvent],
                Callable[..., DomainEvent],
            ],
        ] = {}

    def register_translation[T: IntegrationEvent](
        self,
        topic: str,
        integration_class: type[T],
        translator: Callable[[T], DomainEvent],
    ) -> None:
        """Register a translation from a topic to a domain event.

        Each call registers a mapping from *topic* to a concrete
        ``IntegrationEvent`` class and a translator that converts it to a
        ``DomainEvent``. The gateway automatically subscribes to *topic*
        on the underlying ``MessageSubscriber``.

        Parameters
        ----------
        topic:
            The topic or routing key to subscribe to.
        integration_class:
            The ``IntegrationEvent`` subclass to hydrate the payload into.
        translator:
            A callable that receives a hydrated ``IntegrationEvent`` and
            returns a ``DomainEvent``. This is the Anti-Corruption Layer
            — it translates the integration event's primitive data into
            rich domain types.
        """
        self._registry[topic] = (integration_class, translator)

        async def _handle(payload: dict[str, Any]) -> None:
            await self._process_message(topic, payload)

        self._subscriber.subscribe(topic, _handle)

    async def start(self) -> None:
        """Start the underlying subscriber."""
        await self._subscriber.start()

    async def stop(self) -> None:
        """Stop the underlying subscriber."""
        await self._subscriber.stop()

    async def _process_message(self, topic: str, payload: dict[str, Any]) -> None:
        """Process a raw message from the subscriber.

        Steps
        -----
        1. Look up the integration class and translator for *topic*.
        2. Hydrate the payload into an ``IntegrationEvent`` via
           ``model_validate``.
        3. Translate the integration event to a ``DomainEvent``.
        4. Dispatch the domain event into the ``MessageBus``.

        Parameters
        ----------
        topic:
            The topic the message was received on.
        payload:
            The raw JSON dict payload.
        """
        entry = self._registry.get(topic)
        if entry is None:
            logger.warning(
                "No translation registered for topic '%s' — discarding message", topic
            )
            return

        integration_class, translator = entry

        # Hydrate raw payload into the typed IntegrationEvent.
        try:
            integration_event = integration_class.model_validate(payload)
        except ValidationError:
            logger.exception(
                "Failed to validate payload for %s on topic '%s' — discarding",
                integration_class.__name__,
                topic,
            )
            return

        # Translate (Anti-Corruption Layer) to a DomainEvent.
        try:
            domain_event = translator(integration_event)
        except Exception:
            logger.exception(
                "Translation failed for %s on topic '%s' — discarding",
                integration_class.__name__,
                topic,
            )
            return

        # Dispatch into the internal message bus.
        try:
            await self._message_bus.dispatch(domain_event)
        except Exception:
            logger.exception(
                "Dispatch failed for %s (from %s) on '%s' — propagating",
                type(domain_event).__name__,
                integration_class.__name__,
                topic,
            )
            raise
