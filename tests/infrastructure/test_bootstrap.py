"""Tests for the bootstrap() DI composition root (DCE-46).

Covers Application creation, default dependency injection, bus delegation,
broker lifecycle, uow validation, export visibility, the public accessors
and a partial start (DCE-121).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from pydomain.cqrs import (
    Command,
    EmptyCommandResult,
    Query,
    QueryResult,
)
from pydomain.es import EventStore
from pydomain.infrastructure import Application, EventRegistry, MessageBus, bootstrap
from pydomain.infrastructure.message_subscriber import InboundEventGateway
from pydomain.testing import (
    FakeEventStore,
    FakeSnapshotStore,
    FakeUnitOfWork,
    InMemoryMessageBroker,
    InMemoryMessageSubscriber,
)
from tests.support.fault_injection import FaultInjector
from tests.support.ordering import OrderingSpy

# ── Test types (prefixed with underscore to avoid pytest collection) ──


class _Cmd(Command[EmptyCommandResult]):
    """Simple test command with no payload."""


class _QryRes(QueryResult):
    """Simple query result with a single string value."""

    value: str


class _Qry(Query[_QryRes]):
    """Simple test query with a data parameter."""

    data: str


class _SpyBroker:
    """Minimal MessageBroker spy that records whether start() was called."""

    def __init__(self) -> None:
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def publish(self, topic: str, event: Any) -> None:
        pass

    async def stop(self) -> None:
        pass


# ═══════════════════════════════════════════════════════════════════════
# DCE-46: bootstrap() composition root
# ═══════════════════════════════════════════════════════════════════════


class TestBootstrap:
    """bootstrap() creates a configured Application."""

    @pytest.mark.anyio
    async def test_returns_application(self) -> None:
        """bootstrap(event_store=mock) returns an Application instance."""
        app = await bootstrap(event_store=MagicMock())
        assert isinstance(app, Application)

    @pytest.mark.anyio
    async def test_creates_default_message_bus(self) -> None:
        """When message_bus is None, a MessageBus is created internally."""
        app = await bootstrap(event_store=MagicMock())
        assert isinstance(app._message_bus, MessageBus)

    @pytest.mark.anyio
    async def test_custom_bus_is_reused(self) -> None:
        """Passing a specific MessageBus instance is used by the Application."""
        bus = MessageBus()
        app = await bootstrap(event_store=MagicMock(), message_bus=bus)
        assert app._message_bus is bus

    @pytest.mark.anyio
    async def test_broker_start_is_called(self) -> None:
        """Passing a MessageBroker mock calls start() during bootstrap."""
        broker = _SpyBroker()
        await bootstrap(event_store=MagicMock(), message_broker=broker)
        assert broker.started

    @pytest.mark.anyio
    async def test_dispatch_delegates_command_to_bus(self) -> None:
        """app.dispatch(command) returns a CommandResult."""
        bus = MessageBus()

        async def handler(cmd: _Cmd, uow: Any = None) -> EmptyCommandResult:
            return EmptyCommandResult()

        bus.register_command(_Cmd, handler, uow_factory=lambda: FakeUnitOfWork())

        app = await bootstrap(event_store=MagicMock(), message_bus=bus)
        result = await app.dispatch(_Cmd())

        assert isinstance(result, EmptyCommandResult)

    @pytest.mark.anyio
    async def test_dispatch_delegates_query_to_bus(self) -> None:
        """app.dispatch(query) returns the expected typed result."""
        bus = MessageBus()

        async def handler(query: _Qry) -> _QryRes:
            return _QryRes(value=query.data)

        bus.register_query(_Qry, handler)

        app = await bootstrap(event_store=MagicMock(), message_bus=bus)
        result = await app.dispatch(_Qry(data="test-value"))

        assert isinstance(result, _QryRes)
        assert result.value == "test-value"

    @pytest.mark.anyio
    async def test_event_registry_created_by_default(self) -> None:
        """If event_registry is not passed, one is created."""
        app = await bootstrap(event_store=MagicMock())
        assert isinstance(app._event_registry, EventRegistry)

    @pytest.mark.anyio
    async def test_custom_event_registry_is_reused(self) -> None:
        """A pre-configured EventRegistry is used by the Application."""
        registry = EventRegistry()
        app = await bootstrap(event_store=MagicMock(), event_registry=registry)
        assert app._event_registry is registry

    @pytest.mark.anyio
    async def test_in_memory_broker_start(self) -> None:
        """Using InMemoryMessageBroker calls start() successfully."""
        broker = InMemoryMessageBroker()
        app = await bootstrap(event_store=MagicMock(), message_broker=broker)
        assert isinstance(app, Application)

    def test_application_exported(self) -> None:
        """Application is in pydomain.infrastructure.__all__."""
        from pydomain.infrastructure import __all__ as infra_all

        assert "Application" in infra_all

    def test_bootstrap_exported(self) -> None:
        """bootstrap is in pydomain.infrastructure.__all__."""
        from pydomain.infrastructure import __all__ as infra_all

        assert "bootstrap" in infra_all

    # ═══════════════════════════════════════════════════════════════════════
    # DCE-85: EventStore protocol type hints
    # ═══════════════════════════════════════════════════════════════════════

    @pytest.mark.anyio
    async def test_bootstrap_without_event_store(self) -> None:
        """bootstrap(event_store=None) returns an Application."""
        app = await bootstrap(event_store=None)
        assert isinstance(app, Application)

    def test_event_store_protocol_importable(self) -> None:
        """EventStore protocol is importable from pydomain.es."""
        assert isinstance(EventStore, type)

    # ═══════════════════════════════════════════════════════════════════════
    # DCE-87: Snapshot store bootstrap wiring
    # ═══════════════════════════════════════════════════════════════════════

    @pytest.mark.anyio
    async def test_snapshot_store_passed_through(self) -> None:
        """bootstrap(snapshot_store=FakeSnapshotStore()) passes instance to
        the Application.
        """
        store = FakeSnapshotStore()
        app = await bootstrap(event_store=FakeEventStore(), snapshot_store=store)
        assert app.snapshot_store is store

    @pytest.mark.anyio
    async def test_snapshot_store_defaults_to_none(self) -> None:
        """bootstrap() without snapshot_store produces app.snapshot_store
        is None.
        """
        app = await bootstrap(event_store=FakeEventStore())
        assert app.snapshot_store is None

    @pytest.mark.anyio
    async def test_snapshot_store_logged(self, caplog: Any) -> None:
        """Log message includes the snapshot store type name."""
        caplog.set_level("INFO")
        store = FakeSnapshotStore()
        await bootstrap(event_store=FakeEventStore(), snapshot_store=store)
        assert "FakeSnapshotStore" in caplog.text


# ═══════════════════════════════════════════════════════════════════════
# DCE-121: bootstrap wiring and the Application's public accessors
# ═══════════════════════════════════════════════════════════════════════


class TestApplicationAccessors:
    """INF-17: the Application exposes the bus and registry it was built with."""

    @pytest.mark.anyio
    async def test_application_exposes_message_bus_and_event_registry(self) -> None:
        """Given a bus and a registry, when bootstrapped, then the Application
        returns those same objects and does not let them be replaced.
        """
        bus = MessageBus()
        registry = EventRegistry()

        app = await bootstrap(message_bus=bus, event_registry=registry)

        assert app.message_bus is bus
        assert app.event_registry is registry
        with pytest.raises(AttributeError):
            app.message_bus = MessageBus()  # type: ignore[misc]
        with pytest.raises(AttributeError):
            app.event_registry = EventRegistry()  # type: ignore[misc]

    @pytest.mark.anyio
    async def test_application_exposes_the_bus_and_registry_bootstrap_created(
        self,
    ) -> None:
        """Given no bus and no registry, when bootstrapped, then the
        Application exposes the ones bootstrap() created.
        """
        app = await bootstrap()

        assert isinstance(app.message_bus, MessageBus)
        assert isinstance(app.event_registry, EventRegistry)

    def test_application_creates_an_event_registry_when_given_none(self) -> None:
        """Given an Application built directly without a registry, then it
        exposes a new EventRegistry rather than None (decision-log B-28).
        """
        app = Application(message_bus=MessageBus())

        assert isinstance(app.event_registry, EventRegistry)


# Labels of the parts bootstrap() starts, in its start order, and the order
# Application.shutdown() stops them in (ADR-047; ADR-073).
_START_ORDER = ("inbound1", "inbound2", "broker", "outbound")
_SHUTDOWN_ORDER = ("inbound1", "inbound2", "outbound", "broker")


class _Lifecycle:
    """A part with no-op start() and stop(), standing in for an outbound
    gateway so that no relay task is left running when a stop is skipped.
    """

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


def _parts(
    spy: OrderingSpy,
    bus: MessageBus,
    faults: dict[str, tuple[str, BaseException]] | None = None,
) -> dict[str, Any]:
    """The four parts bootstrap() starts, each recorded by *spy*.

    *faults* maps a label to the method that raises and its exception. The
    fault is raised before the call reaches the part, so the spy does not
    record it.
    """
    parts: dict[str, Any] = {
        "inbound1": InboundEventGateway(InMemoryMessageSubscriber(), bus),
        "inbound2": InboundEventGateway(InMemoryMessageSubscriber(), bus),
        "broker": InMemoryMessageBroker(),
        "outbound": _Lifecycle(),
    }
    wrapped = {
        label: spy.wrap(part, label, methods={"start", "stop"})
        for label, part in parts.items()
    }
    for label, (method, exc) in (faults or {}).items():
        wrapped[label] = FaultInjector(
            wrapped[label], at=1, raises=exc, methods={method}
        ).proxy
    return wrapped


async def _bootstrap(bus: MessageBus, parts: dict[str, Any]) -> Application:
    return await bootstrap(
        message_bus=bus,
        inbound_gateways=[parts["inbound1"], parts["inbound2"]],
        message_broker=parts["broker"],
        outbound_gateway=parts["outbound"],
    )


def _stops(spy: OrderingSpy) -> list[str]:
    return [entry for entry in spy.entries if ".stop" in entry]


def _notes(exc: BaseException) -> list[str]:
    return list(getattr(exc, "__notes__", []))


class TestWiringCheck:
    """INF-16: bootstrap() refuses a gateway that dispatches into another bus."""

    @pytest.mark.anyio
    async def test_bootstrap_raises_when_gateway_bus_differs(self) -> None:
        """Given a second gateway built on another bus, when bootstrap() runs,
        then it raises ValueError naming that gateway, before starting anything.
        """
        bus = MessageBus()
        first = InMemoryMessageSubscriber()
        second = InMemoryMessageSubscriber()
        broker = _SpyBroker()

        with pytest.raises(
            ValueError, match=r"inbound_gateways\[1\] \(InboundEventGateway\)"
        ):
            await bootstrap(
                message_bus=bus,
                inbound_gateways=[
                    InboundEventGateway(first, bus),
                    InboundEventGateway(second, MessageBus()),
                ],
                message_broker=broker,
            )

        assert (first.started, second.started, broker.started) == (False,) * 3

    @pytest.mark.anyio
    async def test_bootstrap_raises_when_message_bus_is_omitted_with_gateways(
        self,
    ) -> None:
        """Given a gateway and no message_bus=, when bootstrap() runs, then it
        raises ValueError instead of building a bus the gateway does not use.
        """
        subscriber = InMemoryMessageSubscriber()

        with pytest.raises(
            ValueError, match=r"inbound_gateways\[0\] \(InboundEventGateway\)"
        ):
            await bootstrap(
                inbound_gateways=[InboundEventGateway(subscriber, MessageBus())]
            )

        assert subscriber.started is False

    @pytest.mark.anyio
    async def test_bootstrap_accepts_gateways_on_the_application_bus(self) -> None:
        """Given gateways built on the bus passed as message_bus=, then
        bootstrap() starts them and the Application dispatches into that bus.
        """
        bus = MessageBus()
        subscriber = InMemoryMessageSubscriber()
        gateway = InboundEventGateway(subscriber, bus)

        app = await bootstrap(message_bus=bus, inbound_gateways=[gateway])

        assert app.inbound_gateways == [gateway]
        assert subscriber.started is True
        await app.shutdown()


class TestPartialStart:
    """INF-06: a start that fails part-way stops what it had started."""

    @pytest.mark.parametrize("failing", ["inbound2", "broker", "outbound"])
    @pytest.mark.anyio
    async def test_partial_start_stops_started_parts(self, failing: str) -> None:
        """Given a part whose start() raises, when bootstrap() runs, then the
        start error propagates and every part started before it is stopped,
        in shutdown order; the part that failed is not stopped.
        """
        spy = OrderingSpy()
        bus = MessageBus()
        error = ConnectionError("synthetic start failure")
        parts = _parts(spy, bus, {failing: ("start", error)})

        with pytest.raises(ConnectionError) as raised:
            await _bootstrap(bus, parts)

        assert raised.value is error
        started = _START_ORDER[: _START_ORDER.index(failing)]
        assert [e for e in spy.entries if e.endswith(".start")] == [
            f"{label}.start" for label in started
        ]
        assert _stops(spy) == [
            f"{label}.stop" for label in _SHUTDOWN_ORDER if label in started
        ]

    @pytest.mark.anyio
    async def test_partial_start_notes_a_cleanup_stop_that_fails(self) -> None:
        """Given a failed start and a clean-up stop that raises too, then the
        start error propagates with a note naming the clean-up error's type
        only, and the remaining clean-up stops still run.
        """
        spy = OrderingSpy()
        bus = MessageBus()
        error = ConnectionError("synthetic start failure")
        parts = _parts(
            spy,
            bus,
            {
                "broker": ("start", error),
                "inbound1": ("stop", RuntimeError("synthetic order 42")),
            },
        )

        with pytest.raises(ConnectionError) as raised:
            await _bootstrap(bus, parts)

        assert raised.value is error
        assert _notes(error) == ["clean-up also failed: RuntimeError"]
        assert _stops(spy) == ["inbound2.stop"]

    @pytest.mark.anyio
    async def test_partial_start_lets_a_cancelled_cleanup_through(self) -> None:
        """Given a failed start and a clean-up stop that is cancelled, then the
        cancellation propagates at once and no later clean-up stop runs.
        """
        spy = OrderingSpy()
        bus = MessageBus()
        parts = _parts(
            spy,
            bus,
            {
                "broker": ("start", ConnectionError("synthetic start failure")),
                "inbound1": ("stop", asyncio.CancelledError()),
            },
        )

        with pytest.raises(asyncio.CancelledError):
            await _bootstrap(bus, parts)

        assert _stops(spy) == []


class TestShutdown:
    """INF-07: shutdown runs every stop, in ADR-047 order."""

    @pytest.mark.anyio
    async def test_shutdown_stops_every_part_in_order(self) -> None:
        """Given a started application, when it shuts down, then the inbound
        gateways stop first, then the outbound gateway, then the broker.
        """
        spy = OrderingSpy()
        bus = MessageBus()
        app = await _bootstrap(bus, _parts(spy, bus))

        await app.shutdown()

        assert _stops(spy) == [f"{label}.stop" for label in _SHUTDOWN_ORDER]

    @pytest.mark.anyio
    async def test_shutdown_continues_after_a_failing_stop(self) -> None:
        """Given an inbound gateway whose stop() raises, when the application
        shuts down, then every later part is still stopped and the error
        propagates.
        """
        spy = OrderingSpy()
        bus = MessageBus()
        error = RuntimeError("synthetic stop failure")
        app = await _bootstrap(bus, _parts(spy, bus, {"inbound1": ("stop", error)}))

        with pytest.raises(RuntimeError) as raised:
            await app.shutdown()

        assert raised.value is error
        assert _stops(spy) == ["inbound2.stop", "outbound.stop", "broker.stop"]

    @pytest.mark.anyio
    async def test_shutdown_raises_the_first_error_noting_later_ones(self) -> None:
        """Given two stops that raise, when the application shuts down, then
        the first error propagates with a note naming the later error's type
        only, never its message.
        """
        spy = OrderingSpy()
        bus = MessageBus()
        first = RuntimeError("synthetic order 42")
        parts = _parts(
            spy,
            bus,
            {
                "inbound2": ("stop", first),
                "broker": ("stop", OSError("synthetic invoice 7")),
            },
        )
        app = await _bootstrap(bus, parts)

        with pytest.raises(RuntimeError) as raised:
            await app.shutdown()

        assert raised.value is first
        assert _notes(first) == ["clean-up also failed: OSError"]
        assert _stops(spy) == ["inbound1.stop", "outbound.stop"]

    @pytest.mark.anyio
    async def test_shutdown_lets_cancellation_through_at_once(self) -> None:
        """Given a stop that is cancelled, when the application shuts down,
        then the cancellation propagates and no later stop runs.
        """
        spy = OrderingSpy()
        bus = MessageBus()
        parts = _parts(spy, bus, {"inbound1": ("stop", asyncio.CancelledError())})
        app = await _bootstrap(bus, parts)

        with pytest.raises(asyncio.CancelledError):
            await app.shutdown()

        assert _stops(spy) == []
