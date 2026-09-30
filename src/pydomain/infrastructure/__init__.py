"""Infrastructure package for DDD/CQRS/ES library.

Provides concrete infrastructure implementations. Interfaces and
abstract base classes live in the CQRS layer as Clean Architecture ports.
"""

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import (
    OutboundEventRegistry,
    OutboxEntry,
    OutboxStore,
    OutboxWriter,
)
from pydomain.infrastructure.bootstrap import Application, bootstrap
from pydomain.infrastructure.event_registry import EventRegistry, GenericDomainEvent
from pydomain.infrastructure.message_broker import MessageBroker, OutboundEventGateway
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.infrastructure.message_subscriber import (
    InboundEventGateway,
    MessageSubscriber,
)
from pydomain.infrastructure.subscription import Subscription, SubscriptionRunner

__all__ = [
    "Application",
    "bootstrap",
    "EventRegistry",
    "GenericDomainEvent",
    "InboundEventGateway",
    "IntegrationEvent",
    "MessageBroker",
    "MessageBus",
    "MessageSubscriber",
    "OutboundEventGateway",
    "OutboundEventRegistry",
    "OutboxEntry",
    "OutboxStore",
    "OutboxWriter",
    "Subscription",
    "SubscriptionRunner",
]
