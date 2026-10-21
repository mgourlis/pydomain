"""Tests for the bootstrap() DI composition root (DCE-46).

Covers Application creation, default dependency injection, bus delegation,
broker lifecycle, uow validation, export visibility, the public accessors
and a partial start (DCE-121).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from pydomain.cqrs import (
    Command,
    EmptyCommandResult,
    Query,
    QueryResult,
)
from pydomain.cqrs.outbox import OutboundEventRegistry
from pydomain.es import EventStore
from pydomain.infrastructure import Application, EventRegistry, MessageBus, bootstrap
from pydomain.infrastructure.message_broker import OutboundEventGateway
from pydomain.infrastructure.message_subscriber import InboundEventGateway
from pydomain.testing import (
    FakeEventStore,
    FakeOutboxStore,
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

    @pytest.mark.xfail(
        strict=True,
        raises=AttributeError,
        reason="DCE-121 INF-17: Application has no message_bus or event_registry",
    )
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

    @pytest.mark.xfail(
        strict=True,
        raises=AttributeError,
        reason="DCE-121 INF-17: Application has no message_bus or event_registry",
    )
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


# Labels of the parts bootstrap() starts, in its start order (ADR-047).
_START_ORDER = ("inbound1", "inbound2", "broker", "outbound")


class TestPartialStart:
    """INF-06: a start that fails part-way stops what it had started."""

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-121 INF-06: a failed start leaves the started parts running",
    )
    @pytest.mark.parametrize("failing", ["inbound2", "broker", "outbound"])
    @pytest.mark.anyio
    async def test_partial_start_stops_started_parts(self, failing: str) -> None:
        """Given a part whose start() raises, when bootstrap() runs, then the
        start error propagates and every part started before it was stopped.
        """
        spy = OrderingSpy()
        bus = MessageBus()
        broker = InMemoryMessageBroker()
        parts: dict[str, Any] = {
            "inbound1": InboundEventGateway(InMemoryMessageSubscriber(), bus),
            "inbound2": InboundEventGateway(InMemoryMessageSubscriber(), bus),
            "broker": broker,
            "outbound": OutboundEventGateway(
                FakeOutboxStore(), broker, OutboundEventRegistry()
            ),
        }
        wrapped = {
            label: spy.wrap(part, label, methods={"start", "stop"})
            for label, part in parts.items()
        }
        wrapped[failing] = FaultInjector(
            wrapped[failing],
            at=1,
            raises=ConnectionError("synthetic start failure"),
            methods={"start"},
        ).proxy

        with pytest.raises(ConnectionError):
            await bootstrap(
                message_bus=bus,
                inbound_gateways=[wrapped["inbound1"], wrapped["inbound2"]],
                message_broker=wrapped["broker"],
                outbound_gateway=wrapped["outbound"],
            )

        started = _START_ORDER[: _START_ORDER.index(failing)]
        assert [f"{label}.start" for label in started] == [
            entry for entry in spy.entries if entry.endswith(".start")
        ]
        not_stopped = [label for label in started if f"{label}.stop" not in spy.entries]
        assert not_stopped == [], f"left running: {not_stopped}; log {spy.entries}"
