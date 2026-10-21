"""Dependency injection composition root.

The ``bootstrap()`` function wires together event store, message bus,
repositories, handlers, and projections into a configured ``Application``
object. Tests call it with fakes; production calls it with real adapters.
The handlers don't change.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, NoReturn, Protocol

from pydomain.cqrs.commands import Command
from pydomain.cqrs.queries import Query
from pydomain.ddd.domain_event import DomainEvent
from pydomain.es import EventStore
from pydomain.es.snapshot import SnapshotStore
from pydomain.infrastructure.event_registry import EventRegistry
from pydomain.infrastructure.message_broker import MessageBroker, OutboundEventGateway
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.infrastructure.message_subscriber import InboundEventGateway

logger = logging.getLogger("pydomain.bootstrap")


class _Stoppable(Protocol):
    async def stop(self) -> None: ...


class Application:
    """Configured application entry point.

    Wraps a ``MessageBus`` and provides ``dispatch()``
    for unified command and query dispatch.

    Parameters
    ----------
    message_bus:
        Configured MessageBus instance with registered handlers.
    event_registry:
        EventRegistry for serialization support. A new, empty one is
        created when omitted.
    snapshot_store:
        Optional snapshot store adapter for snapshot-aware repositories.
    message_broker:
        Optional broker the outbound gateway publishes through.
    inbound_gateways:
        Gateways managing external consumer lines. The Application keeps its
        own copy of the list.
    outbound_gateway:
        Optional gateway draining the transactional outbox into the broker.
    """

    def __init__(
        self,
        message_bus: MessageBus,
        event_registry: EventRegistry | None = None,
        snapshot_store: SnapshotStore | None = None,
        message_broker: MessageBroker | None = None,
        inbound_gateways: list[InboundEventGateway] | None = None,
        outbound_gateway: OutboundEventGateway | None = None,
    ) -> None:
        self._message_bus = message_bus
        self._event_registry = (
            event_registry if event_registry is not None else EventRegistry()
        )
        self._snapshot_store = snapshot_store
        self._message_broker = message_broker
        self._inbound_gateways = list(inbound_gateways or [])
        self._outbound_gateway = outbound_gateway

    @property
    def message_bus(self) -> MessageBus:
        """Return the message bus that ``dispatch()`` routes through.

        Register handlers on it after bootstrap, e.g.
        ``app.message_bus.register_event(OrderPlaced, handler)``.
        """
        return self._message_bus

    @property
    def event_registry(self) -> EventRegistry:
        """Return the event registry used for serialization."""
        return self._event_registry

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
        """Return a new list of the registered inbound gateways."""
        return list(self._inbound_gateways)

    @property
    def outbound_gateway(self) -> OutboundEventGateway | None:
        """Return the outbound event gateway instance, if any."""
        return self._outbound_gateway

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

        Stops inbound message consumers to completely drain incoming traffic,
        then drains the outbound gateway before tearing down the message
        broker.  The gateway must stop **before** the broker it publishes
        through, or its final batch has nowhere to go.

        Every stop runs even when an earlier one raises an ``Exception``.
        The first such error is re-raised after the last stop, with a note
        ``clean-up also failed: <type>`` for each later one; the notes name
        the exception type only, never its message. A ``BaseException``
        that is not an ``Exception``, such as ``asyncio.CancelledError``,
        propagates at once and skips the remaining stops.

        Raises
        ------
        Exception
            The first exception a stop raised.
        """
        await _stop_in_order(
            self._inbound_gateways, self._outbound_gateway, self._message_broker
        )


async def _stop_in_order(
    inbound_gateways: list[InboundEventGateway],
    outbound_gateway: OutboundEventGateway | None,
    message_broker: MessageBroker | None,
) -> None:
    """Stop the parts in shutdown order and raise the first stop error.

    The inbound gateways stop first, as they always have, so no new work
    arrives; then the outbound gateway, before the broker it publishes
    through (ADR-063 point 7). Every stop runs even when one raises an
    ``Exception``. Afterwards the first stop error is raised with a
    type-only note for each one after it (ADR-073). Any other
    ``BaseException`` propagates at once.
    """
    failures = await _stop_each(inbound_gateways, outbound_gateway, message_broker)
    if failures:
        _raise_noted(failures[0], failures[1:])


def _note(error: BaseException, failures: list[Exception]) -> None:
    """Add a type-only note to *error* for each of *failures*."""
    for failure in failures:
        error.add_note(f"clean-up also failed: {type(failure).__name__}")


def _raise_noted(error: BaseException, failures: list[Exception]) -> NoReturn:
    """Raise *error*, noting each of *failures* by type."""
    _note(error, failures)
    raise error


async def _stop_each(
    inbound_gateways: list[InboundEventGateway],
    outbound_gateway: OutboundEventGateway | None,
    message_broker: MessageBroker | None,
) -> list[Exception]:
    """Stop every part in shutdown order; return the exceptions the stops raised."""
    if inbound_gateways:
        logger.info("Stopping %d inbound event gateway(s)...", len(inbound_gateways))
    parts: list[tuple[str | None, _Stoppable]] = [
        (None, gateway) for gateway in inbound_gateways
    ]
    if outbound_gateway is not None:
        parts.append(("Draining outbound event gateway...", outbound_gateway))
    if message_broker is not None:
        parts.append(("Stopping outbound message broker...", message_broker))

    failures: list[Exception] = []
    for message, part in parts:
        if message is not None:
            logger.info(message)
        try:
            await part.stop()
        except Exception as exc:
            failures.append(exc)
    return failures


# What a start clean-up returns: the stop errors, and the BaseException that
# ended it early (neither a cancellation nor an interrupt), if any.
_Outcome = tuple[list[Exception], BaseException | None]

# Start clean-ups that are still running: the strong reference keeps an
# abandoned clean-up from being garbage-collected mid-stop. The ones that
# bootstrap() stopped waiting for report their own failures.
_CLEAN_UPS: set[asyncio.Task[_Outcome]] = set()
_ABANDONED: set[asyncio.Task[_Outcome]] = set()


def _start_clean_up(
    started_gateways: list[InboundEventGateway],
    started_broker: MessageBroker | None,
) -> asyncio.Task[_Outcome]:
    """Stop what a failed or cancelled start had started, as its own task.

    The outbound gateway starts last, so it never needs stopping here.
    """
    task = asyncio.ensure_future(_clean_up(started_gateways, started_broker))
    _CLEAN_UPS.add(task)
    task.add_done_callback(_clean_up_done)
    return task


async def _clean_up(
    started_gateways: list[InboundEventGateway],
    started_broker: MessageBroker | None,
) -> _Outcome:
    """Run the stops; return a stop's other ``BaseException`` instead of raising.

    A shielded task that raises after its waiter left is logged by asyncio
    (Python 3.14) with the exception's message; returning the error keeps
    that message out of the logs. Cancellations, interrupts and exits still
    propagate.
    """
    try:
        return await _stop_each(started_gateways, None, started_broker), None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except BaseException as error:
        return [], error


async def _wait_for_clean_up(task: asyncio.Task[_Outcome]) -> list[Exception]:
    """Wait for *task*, shielded, and return its stop errors.

    A cancellation stops the wait, not the task, which then reports its own
    failures. A ``BaseException`` that ended the clean-up is raised here.
    """
    try:
        failures, error = await asyncio.shield(task)
    except asyncio.CancelledError:
        if task in _CLEAN_UPS:
            _ABANDONED.add(task)
        raise
    if error is not None:
        raise error
    return failures


def _clean_up_done(task: asyncio.Task[_Outcome]) -> None:
    """Drop the reference; log an abandoned clean-up's failures by type."""
    _CLEAN_UPS.discard(task)
    abandoned = task in _ABANDONED
    _ABANDONED.discard(task)
    if not abandoned or task.cancelled() or task.exception() is not None:
        return
    failures, error = task.result()
    if error is not None:
        logger.warning("Abandoned start clean-up failed with %s", type(error).__name__)
    for failure in failures:
        logger.warning(
            "Abandoned start clean-up: a stop failed with %s", type(failure).__name__
        )


async def bootstrap(
    event_store: EventStore | None = None,
    snapshot_store: SnapshotStore | None = None,
    message_bus: MessageBus | None = None,
    message_broker: MessageBroker | None = None,
    outbound_gateway: OutboundEventGateway | None = None,
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
        Optional pre-configured ``MessageBus``. Required when
        *inbound_gateways* is given: it must be the bus every gateway was
        built with.
    message_broker:
        Optional ``MessageBroker`` for publishing integration events.
    outbound_gateway:
        Optional ``OutboundEventGateway`` draining the transactional outbox.
    event_registry:
        Optional ``EventRegistry`` for serialization support.
    inbound_gateways:
        Optional list of ``InboundEventGateway`` instances to activate, each
        listed once.

    Returns
    -------
    Application
        A configured Application with unified entry points.

    Raises
    ------
    ValueError
        If an inbound gateway dispatches into a bus other than the
        application's, including when *message_bus* is omitted, or if the
        same gateway is listed twice. Nothing has been started.
    Exception
        Whatever a ``start()`` raised. The parts started before it are
        stopped first, in shutdown order; the part whose ``start()`` raised
        is not. A clean-up stop that raises too adds a note
        ``clean-up also failed: <type>`` to the start error. If
        ``bootstrap()`` is cancelled during that clean-up, it finishes the
        clean-up and raises the cancellation, with the start error as its
        context.
    asyncio.CancelledError
        If ``bootstrap()`` is cancelled while starting. The parts started
        before the cancelled ``start()`` are stopped first, in shutdown
        order and shielded from the cancellation (``asyncio.shield``), with
        the same notes. A second cancellation stops waiting for that
        clean-up but does not cancel it; the clean-up then logs its failures
        by type. ``KeyboardInterrupt`` and ``SystemExit`` propagate at once,
        without clean-up.
    """
    bus = message_bus if message_bus is not None else MessageBus()
    gateways = inbound_gateways or []

    # 0. Refuse a gateway that would dispatch into another bus (its events
    #    would never reach the handlers registered on the application's bus)
    #    or that is listed twice (it would be started and stopped twice).
    positions: dict[int, int] = {}
    for index, gateway in enumerate(gateways):
        name = type(gateway).__name__
        if gateway.message_bus is not bus:
            raise ValueError(
                f"inbound_gateways[{index}] ({name}) dispatches into a different "
                "MessageBus than the application's; pass the bus the gateway "
                "was built with as bootstrap(message_bus=...)"
            )
        first = positions.setdefault(id(gateway), index)
        if first != index:
            raise ValueError(
                f"inbound_gateways[{index}] ({name}) is the same gateway as "
                f"inbound_gateways[{first}]; list each gateway once"
            )

    started_gateways: list[InboundEventGateway] = []
    started_broker: MessageBroker | None = None
    try:
        # 1. Start inbound pipelines so consumers are ready to handle work.
        for gateway in gateways:
            await gateway.start()
            started_gateways.append(gateway)

        # 2. Start outbound channels. The broker starts before the gateway,
        #    which publishes through it.
        if message_broker is not None:
            await message_broker.start()
            started_broker = message_broker
        if outbound_gateway is not None:
            await outbound_gateway.start()
    except Exception as exc:
        # 3. Stop what started, in shutdown order, then re-raise the start
        #    error. A cancellation during the clean-up does not interrupt it:
        #    finish it, then re-raise the cancellation (B-45).
        clean_up = _start_clean_up(started_gateways, started_broker)
        try:
            failures = await _wait_for_clean_up(clean_up)
        except asyncio.CancelledError:
            _ABANDONED.discard(clean_up)  # still waited for below (B-45)
            _note(exc, await _wait_for_clean_up(clean_up))
            raise
        _raise_noted(exc, failures)
    except asyncio.CancelledError as cancelled:
        # 4. Cancelled while starting: stop what started, shielded from the
        #    cancellation, then re-raise it (B-40). Interrupts and exits
        #    propagate at once (B-41).
        clean_up = _start_clean_up(started_gateways, started_broker)
        _raise_noted(cancelled, await _wait_for_clean_up(clean_up))

    gateway_names = (
        ", ".join(type(g).__name__ for g in gateways) if gateways else "None"
    )
    logger.info(
        "Application bootstrapped: event_store=%s, snapshot_store=%s, "
        "broker=%s, inbound_gateways=[%s], outbound_gateway=%s",
        type(event_store).__name__ if event_store else "None",
        type(snapshot_store).__name__ if snapshot_store else "None",
        type(message_broker).__name__ if message_broker else "None",
        gateway_names,
        type(outbound_gateway).__name__ if outbound_gateway else "None",
    )

    return Application(
        message_bus=bus,
        event_registry=event_registry,
        snapshot_store=snapshot_store,
        message_broker=message_broker,
        inbound_gateways=gateways,
        outbound_gateway=outbound_gateway,
    )
