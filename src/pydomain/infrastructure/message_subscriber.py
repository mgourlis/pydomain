"""Message subscriber protocol and inbound event gateway.

The ``MessageSubscriber`` protocol defines the contract for receiving
integration events from external brokers (Kafka, RabbitMQ).  The
``InboundEventGateway`` bridges the subscriber side by hydrating raw
JSON payloads into typed ``IntegrationEvent`` instances, reading any
tracing metadata (``correlation_id``, ``causation_id``), translating
them to ``DomainEvent`` instances, and dispatching them into the
internal ``MessageBus``.

Message Acknowledgment
----------------------
The gateway distinguishes two failure modes:

- **Validation/translation failures** (bad payload, unknown topic):
  logged and swallowed. A validation failure is logged at ``ERROR`` with
  the failing field locations and error codes, never the payload's values
  and without a traceback (ADR-071). The exception does **not** propagate, so the
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
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError

from pydomain._describe import describe
from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.message_bus import MessageBus

logger = logging.getLogger("pydomain.message_subscriber")

# The namespace of derived inbound event ids (ADR-080 point 1). Never change
# it: stored idempotency keys are built from the ids it derives.
_INBOUND_EVENT_ID_NAMESPACE = uuid5(
    NAMESPACE_URL, "https://github.com/mgourlis/pydomain/inbound-event-id"
)  # 52a1e11d-5cfe-52a6-971b-a1b2961dd04f


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
    topic implies the type), reads tracing metadata when present
    (``correlation_id``, ``causation_id``), translates them to
    ``DomainEvent`` instances via an Anti-Corruption Layer translator, and
    dispatches them into the ``MessageBus`` for internal routing.

    When the integration event carries tracing IDs, the gateway
    automatically stamps them onto the translated domain event via
    :meth:`DomainEvent.stamp`, preserving the cross-service trace chain.

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

    @property
    def message_bus(self) -> MessageBus:
        """Return the bus translated events are dispatched into.

        ``bootstrap()`` requires it to be the application's bus.
        """
        return self._message_bus

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
        4. Stamp the domain event with tracing IDs when present on the
           integration event.
        5. Dispatch the domain event into the ``MessageBus``.

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
        except ValidationError as exc:
            logger.error(
                "Failed to validate payload for %s on topic '%s' — discarding: %s",
                integration_class.__name__,
                topic,
                describe(exc),
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

        # Identify the domain event by the integration event (ADR-080 point
        # 1): every redelivery gets the same event_id, unless the translator
        # set its own. Without an event_id in the payload there is nothing
        # stable to derive from (point 2).
        if "event_id" not in payload:
            logger.warning(
                "No event_id in the payload of %s on topic '%s' — "
                "dispatching without deduplication",
                integration_class.__name__,
                topic,
            )
        elif domain_event._event_id_generated:  # pyright: ignore[reportPrivateUsage]
            domain_event = domain_event.model_copy(
                update={
                    "event_id": uuid5(
                        _INBOUND_EVENT_ID_NAMESPACE, integration_event.event_id
                    )
                }
            )

        # Stamp the domain event with tracing IDs from the integration
        # event.  correlation_id preserves the business process chain.
        # causation_id is set to the integration event's OWN event_id
        # (not its causation_id) because the integration event IS the
        # direct cause of this domain event.
        if integration_event.correlation_id:
            try:
                domain_event = domain_event.stamp(
                    correlation_id=UUID(integration_event.correlation_id),
                    causation_id=UUID(integration_event.event_id),
                )
            except (ValueError, TypeError) as exc:
                # Name the fields that failed to parse, never their values
                # (ADR-071, B-43).
                logger.warning(
                    "Invalid tracing IDs on %s for topic '%s' — %s. "
                    "Dispatching without tracing.",
                    integration_class.__name__,
                    topic,
                    _tracing_problem(
                        exc,
                        correlation_id=integration_event.correlation_id,
                        event_id=integration_event.event_id,
                    ),
                )

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


def _tracing_problem(exc: Exception, **ids: str) -> str:
    """Say which of *ids* are not UUIDs, by field name only.

    When every id parses, the domain event's own fields rejected them (a
    narrower type such as ``UUID4``), so the rejection is described instead.
    """
    names = [name for name, value in ids.items() if not _is_uuid(value)]
    if not names:
        return f"the domain event rejected them: {describe(exc)}"
    if len(names) == 1:
        return f"{names[0]} is not a UUID"
    return f"{' and '.join(names)} are not UUIDs"


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (ValueError, TypeError, AttributeError):
        return False
    return True
