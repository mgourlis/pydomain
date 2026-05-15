"""Dependency injection composition root.

The ``bootstrap()`` function wires together event store, message bus,
repositories, handlers, and projections into a configured ``Application``
object. Tests call it with fakes; production calls it with real adapters.
The handlers don't change.
"""

from __future__ import annotations

import logging
from typing import Any

from pydomain.cqrs.commands import Command, CommandResult
from pydomain.cqrs.queries import Query
from pydomain.cqrs.unit_of_work import UnitOfWork
from pydomain.es import EventStore
from pydomain.es.snapshot import SnapshotStore
from pydomain.infrastructure.event_registry import EventRegistry
from pydomain.infrastructure.message_broker import MessageBroker
from pydomain.infrastructure.message_bus import MessageBus

logger = logging.getLogger("pydomain.bootstrap")


class Application:
    """Configured application entry point.

    Wraps a ``MessageBus`` and provides ``handle()`` and ``query()``
    entry points for command and query dispatch.

    Parameters
    ----------
    message_bus:
        Configured MessageBus instance with registered handlers.
    event_registry:
        Optional EventRegistry for serialization support.
    snapshot_store:
        Optional snapshot store adapter for snapshot-aware repositories.
    """

    def __init__(
        self,
        message_bus: MessageBus,
        event_registry: EventRegistry | None = None,
        snapshot_store: SnapshotStore | None = None,
    ) -> None:
        self._message_bus = message_bus
        self._event_registry = event_registry
        self._snapshot_store = snapshot_store

    @property
    def snapshot_store(self) -> SnapshotStore | None:
        """Return the snapshot store instance, if any."""
        return self._snapshot_store

    async def handle(
        self,
        command: Command[Any],
        uow: UnitOfWork | None = None,
    ) -> CommandResult:
        """Dispatch a command through the message bus.

        Parameters
        ----------
        command:
            The command to dispatch.
        uow:
            Unit of Work. Must be provided -- the underlying
            ``MessageBus.handle()`` raises if ``uow`` is ``None``.

        Returns
        -------
        CommandResult
            The result of command execution.
        """
        return await self._message_bus.handle(command, uow)

    async def query(self, query: Query[Any]) -> Any:
        """Dispatch a query through the message bus.

        Parameters
        ----------
        query:
            The query to dispatch.

        Returns
        -------
        Any
            The query result typed as the query's bound ``TResult``.
        """
        return await self._message_bus.query(query)


async def bootstrap(
    event_store: EventStore | None = None,
    snapshot_store: SnapshotStore | None = None,
    message_bus: MessageBus | None = None,
    message_broker: MessageBroker | None = None,
    event_registry: EventRegistry | None = None,
) -> Application:
    """Wire infrastructure dependencies into a configured Application.

    Parameters
    ----------
    event_store:
        Event store adapter. Uses the ``EventStore`` protocol from
        ``pydomain.es``.
    snapshot_store:
        Optional snapshot store adapter. Same typing rationale as
        ``event_store``.
    message_bus:
        Optional pre-configured ``MessageBus``. A new instance is
        created if not provided.
    message_broker:
        Optional ``MessageBroker`` for publishing integration events.
        If provided, ``start()`` is called during bootstrap.
    event_registry:
        Optional ``EventRegistry`` for serialization support. A new
        instance is created if not provided.

    Returns
    -------
    Application
        A configured Application with ``handle()`` and ``query()``
        entry points.
    """
    bus = message_bus or MessageBus()
    registry = event_registry or EventRegistry()

    if message_broker is not None:
        await message_broker.start()

    logger.info(
        "Application bootstrapped: event_store=%s, snapshot_store=%s, broker=%s",
        type(event_store).__name__,
        type(snapshot_store).__name__ if snapshot_store else "None",
        type(message_broker).__name__ if message_broker else "None",
    )

    return Application(
        message_bus=bus,
        event_registry=registry,
        snapshot_store=snapshot_store,
    )
