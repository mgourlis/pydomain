"""Dependency injection composition root.

The ``bootstrap()`` function wires together event store, message bus,
repositories, handlers, and projections into a configured ``Application``
object. Tests call it with fakes; production calls it with real adapters.
The handlers don't change.
"""

from __future__ import annotations

import logging
from typing import Any

from pydomain.cqrs.commands import Command
from pydomain.cqrs.queries import Query
from pydomain.ddd.domain_event import DomainEvent
from pydomain.es import EventStore
from pydomain.es.snapshot import SnapshotStore
from pydomain.infrastructure.event_registry import EventRegistry
from pydomain.infrastructure.message_broker import MessageBroker
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.infrastructure.message_subscriber import InboundEventGateway

logger = logging.getLogger("pydomain.bootstrap")


class Application:
    """Configured application entry point.

    Wraps a ``MessageBus`` and provides ``dispatch()``
    for unified command and query dispatch.

    Parameters
    ----------
    message_bus:
        Configured MessageBus instance with registered handlers.
    event_registry:
        Optional EventRegistry for serialization support.
    snapshot_store:
        Optional snapshot store adapter for snapshot-aware repositories.
    inbound_gateways:
        Collection of configured gateways managing external consumer lines.
    """

    def __init__(
        self,
        message_bus: MessageBus,
        event_registry: EventRegistry | None = None,
        snapshot_store: SnapshotStore | None = None,
        message_broker: MessageBroker | None = None,
        inbound_gateways: list[InboundEventGateway] | None = None,
    ) -> None:
        self._message_bus = message_bus
        self._event_registry = event_registry
        self._snapshot_store = snapshot_store
        self._message_broker = message_broker
        self._inbound_gateways = inbound_gateways or []

    @property
    def snapshot_store(self) -> SnapshotStore | None:
        """Return the snapshot store instance, if any."""
        return self._snapshot_store

    @property
    def message_broker(self) -> MessageBroker | None:
        """Return the message broker instance, if any."""
        return self._message_broker

    @property
    def inbound_gateways(self) -> list[InboundEventGateway]:
        """Return the registered inbound gateways."""
        return self._inbound_gateways

    async def dispatch(self, message: Command[Any] | Query[Any] | DomainEvent) -> Any:
        """Dispatch a command or query through the message bus.

        Parameters
        ----------
        message:
            The command, query or domain event to dispatch. Commands are
            routed to the CommandBus (which manages UoW lifecycle via the
            registered factory). Queries are routed to the QueryBus.

        Returns
        -------
        Any
            The result of command, query or domain event(None) execution.
        """
        return await self._message_bus.dispatch(message)

    async def shutdown(self) -> None:
        """Gracefully shut down infrastructure resources.

        Stops inbound message consumers to completely drain incoming traffic
        before tearing down the outbound message broker.
        """
        if self._inbound_gateways:
            logger.info(
                "Stopping %d inbound event gateway(s)...",
                len(self._inbound_gateways),
            )
            for gateway in self._inbound_gateways:
                await gateway.stop()

        if self._message_broker is not None:
            logger.info("Stopping outbound message broker...")
            await self._message_broker.stop()


async def bootstrap(
    event_store: EventStore | None = None,
    snapshot_store: SnapshotStore | None = None,
    message_bus: MessageBus | None = None,
    message_broker: MessageBroker | None = None,
    event_registry: EventRegistry | None = None,
    inbound_gateways: list[InboundEventGateway] | None = None,
) -> Application:
    """Wire infrastructure dependencies into a configured Application.

    Parameters
    ----------
    event_store:
        Event store adapter.
    snapshot_store:
        Optional snapshot store adapter.
    message_bus:
        Optional pre-configured ``MessageBus``.
    message_broker:
        Optional ``MessageBroker`` for publishing integration events.
    event_registry:
        Optional ``EventRegistry`` for serialization support.
    inbound_gateways:
        Optional list of ``InboundEventGateway`` instances to activate.

    Returns
    -------
    Application
        A configured Application with unified entry points.
    """
    bus = message_bus or MessageBus()
    registry = event_registry or EventRegistry()
    gateways = inbound_gateways or []

    # 1. Start inbound pipelines so consumers are ready to handle work.
    for gateway in gateways:
        await gateway.start()

    # 2. Start outbound channels.
    if message_broker is not None:
        await message_broker.start()

    gateway_names = (
        ", ".join(type(g).__name__ for g in gateways) if gateways else "None"
    )
    logger.info(
        "Application bootstrapped: event_store=%s, snapshot_store=%s, "
        "broker=%s, inbound_gateways=[%s]",
        type(event_store).__name__ if event_store else "None",
        type(snapshot_store).__name__ if snapshot_store else "None",
        type(message_broker).__name__ if message_broker else "None",
        gateway_names,
    )

    return Application(
        message_bus=bus,
        event_registry=registry,
        snapshot_store=snapshot_store,
        message_broker=message_broker,
        inbound_gateways=gateways,
    )
