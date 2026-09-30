"""Tests for the outbox primitives (OutboxEntry, OutboxStore, OutboundEventRegistry)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import (
    OutboundEventRegistry,
    OutboxEntry,
    OutboxStore,
    OutboxWriter,
)
from pydomain.cqrs.unit_of_work import AbstractUnitOfWork
from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing import FakeOutboxStore, FakeRepository

# ── Sample events ───────────────────────────────────────────────────────


class OrderPlaced(DomainEvent):
    order_id: UUID
    total: int


class InternalNote(DomainEvent):
    note: str


class OrderPlacedIntegration(IntegrationEvent):
    order_id: str
    total: int


def _order_placed() -> OrderPlaced:
    """Build a stamped OrderPlaced domain event."""
    return OrderPlaced(
        event_id=uuid4(),
        occurred_at=datetime.now(UTC),
        correlation_id=uuid4(),
        causation_id=uuid4(),
        order_id=uuid4(),
        total=100,
    )


def _to_integration(event: DomainEvent) -> OrderPlacedIntegration:
    """Translate an OrderPlaced domain event into its integration event."""
    placed = cast(OrderPlaced, event)
    return OrderPlacedIntegration(
        order_id=str(placed.order_id),
        total=placed.total,
    )


def _registry() -> OutboundEventRegistry:
    """Registry with one registered translation."""
    registry = OutboundEventRegistry()
    registry.register_translation(
        OrderPlaced,
        OrderPlacedIntegration,
        "orders.placed",
        _to_integration,
    )
    return registry


# ── OutboxEntry ────────────────────────────────────────────────────────


def _entry() -> OutboxEntry:
    return OutboxEntry(
        message_id="msg-1",
        topic="orders.placed",
        payload={"order_id": "abc", "total": 1},
        occurred_at=datetime.now(UTC),
    )


class TestOutboxEntry:
    """The durable record is immutable and defaults its version to 1."""

    def test_entry_is_frozen(self) -> None:
        entry = _entry()
        with pytest.raises(ValidationError):
            entry.topic = "other"  # type: ignore[misc]

    def test_event_version_defaults_to_one(self) -> None:
        assert _entry().event_version == 1


# ── OutboxStore protocol ───────────────────────────────────────────────


class TestOutboxStoreProtocol:
    """The store port is runtime-checkable."""

    def test_fake_satisfies_protocol(self) -> None:
        assert isinstance(FakeOutboxStore(), OutboxStore)

    def test_unrelated_object_is_not_an_outbox_store(self) -> None:
        assert not isinstance("string", OutboxStore)


# ── OutboundEventRegistry ──────────────────────────────────────────────


class TestOutboundEventRegistryTranslation:
    """Write-path translation from domain events to outbox entries."""

    def test_registered_event_produces_one_entry(self) -> None:
        entries = _registry().to_entries([_order_placed()])
        assert len(entries) == 1

    def test_entry_carries_the_registered_topic(self) -> None:
        entries = _registry().to_entries([_order_placed()])
        assert entries[0].topic == "orders.placed"

    def test_entry_message_id_matches_the_serialized_event_id(self) -> None:
        entry = _registry().to_entries([_order_placed()])[0]
        assert entry.payload["event_id"] == entry.message_id

    def test_entry_payload_is_the_serialized_integration_event(self) -> None:
        event = _order_placed()
        entry = _registry().to_entries([event])[0]

        rehydrated = OrderPlacedIntegration.model_validate(entry.payload)

        assert rehydrated.order_id == str(event.order_id)
        assert rehydrated.total == event.total
        assert rehydrated.event_id == entry.message_id

    def test_entry_propagates_tracing_ids_from_the_domain_event(self) -> None:
        event = _order_placed()
        entries = _registry().to_entries([event])
        assert entries[0].correlation_id == str(event.correlation_id)
        assert entries[0].causation_id == str(event.causation_id)

    def test_entry_carries_the_domain_event_occurred_at(self) -> None:
        event = _order_placed()
        entries = _registry().to_entries([event])
        assert entries[0].occurred_at == event.occurred_at

    def test_unregistered_domain_event_produces_no_entry(self) -> None:
        assert _registry().to_entries([InternalNote(note="hi")]) == []

    def test_translator_returning_none_produces_no_entry(self) -> None:
        registry = OutboundEventRegistry()
        registry.register_translation(
            OrderPlaced,
            OrderPlacedIntegration,
            "orders.placed",
            lambda _event: None,
        )
        assert registry.to_entries([_order_placed()]) == []

    def test_mixed_batch_keeps_only_translatable_events(self) -> None:
        events: list[DomainEvent] = [InternalNote(note="hi"), _order_placed()]
        assert len(_registry().to_entries(events)) == 1


class OrderPlacedIntegrationV2(IntegrationEvent):
    order_id: str
    total: int


def _to_integration_v2(event: DomainEvent) -> OrderPlacedIntegrationV2:
    """Translate an OrderPlaced domain event into the v2 integration event."""
    placed = cast(OrderPlaced, event)
    return OrderPlacedIntegrationV2(
        order_id=str(placed.order_id),
        total=placed.total,
    )


class TestOutboundEventRegistryReregistration:
    """Re-registration overwrites, enabling version hot-swap."""

    def test_second_registration_wins_for_translation(self) -> None:
        registry = _registry()
        registry.register_translation(
            OrderPlaced,
            OrderPlacedIntegration,
            "orders.placed.v2",
            _to_integration,
        )
        entries = registry.to_entries([_order_placed()])
        assert entries[0].topic == "orders.placed.v2"

    def test_second_registration_wins_for_resolution(self) -> None:
        registry = _registry()
        registry.register_translation(
            OrderPlaced,
            OrderPlacedIntegrationV2,
            "orders.placed",
            _to_integration_v2,
        )
        assert registry.resolve("orders.placed") is OrderPlacedIntegrationV2


class TestOutboundEventRegistryResolution:
    """Delivery-path resolution from a stored topic."""

    def test_resolve_returns_the_registered_class(self) -> None:
        assert _registry().resolve("orders.placed") is OrderPlacedIntegration

    def test_resolve_raises_for_unknown_topic(self) -> None:
        with pytest.raises(KeyError):
            _registry().resolve("unknown.topic")

    def test_resolved_class_round_trips_the_stored_payload(self) -> None:
        entry = _registry().to_entries([_order_placed()])[0]
        rehydrated = _registry().resolve(entry.topic).model_validate(entry.payload)
        assert rehydrated.model_dump() == entry.payload


# ── OutboxWriter ───────────────────────────────────────────────────────


def _exploding_registry() -> OutboundEventRegistry:
    """Registry whose translator always fails."""

    def exploding_translator(_event: DomainEvent) -> OrderPlacedIntegration:
        msg = "translator bug"
        raise ValueError(msg)

    registry = OutboundEventRegistry()
    registry.register_translation(
        OrderPlaced,
        OrderPlacedIntegration,
        "orders.placed",
        exploding_translator,
    )
    return registry


class TestOutboxWriter:
    """The write-side join between the registry and the outbox store."""

    @pytest.mark.anyio
    async def test_write_appends_translated_entries(self) -> None:
        store = FakeOutboxStore()

        await OutboxWriter(store, _registry()).write([_order_placed()])

        assert len(store.entries) == 1

    @pytest.mark.anyio
    async def test_write_returns_the_number_appended(self) -> None:
        store = FakeOutboxStore()

        written = await OutboxWriter(store, _registry()).write(
            [_order_placed(), _order_placed()]
        )

        assert written == 2

    @pytest.mark.anyio
    async def test_unregistered_event_appends_nothing(self) -> None:
        store = FakeOutboxStore()

        written = await OutboxWriter(store, _registry()).write(
            [InternalNote(note="hi")]
        )

        assert written == 0
        assert store.entries == []

    @pytest.mark.anyio
    async def test_translator_returning_none_appends_nothing(self) -> None:
        registry = OutboundEventRegistry()
        registry.register_translation(
            OrderPlaced,
            OrderPlacedIntegration,
            "orders.placed",
            lambda _event: None,
        )
        store = FakeOutboxStore()

        written = await OutboxWriter(store, registry).write([_order_placed()])

        assert written == 0
        assert store.entries == []

    @pytest.mark.anyio
    async def test_empty_event_list_skips_the_store_call(self) -> None:
        store = FakeOutboxStore()
        store.fail_next_append = True

        written = await OutboxWriter(store, _registry()).write([])

        assert written == 0
        # The flag is still set, proving append() was never called.
        assert store.fail_next_append is True

    @pytest.mark.anyio
    async def test_translator_failure_propagates(self) -> None:
        writer = OutboxWriter(FakeOutboxStore(), _exploding_registry())

        with pytest.raises(ValueError, match="translator bug"):
            await writer.write([_order_placed()])

    @pytest.mark.anyio
    async def test_translator_failure_appends_nothing(self) -> None:
        store = FakeOutboxStore()
        writer = OutboxWriter(store, _exploding_registry())

        with pytest.raises(ValueError):
            await writer.write([_order_placed()])

        assert store.entries == []


# ── Payload schema version ─────────────────────────────────────────────


class OrderPlacedIntegrationV3(IntegrationEvent):
    event_version: int = 3
    order_id: str
    total: int


def _to_integration_v3(event: DomainEvent) -> OrderPlacedIntegrationV3:
    """Translate an OrderPlaced domain event into the v3 integration event."""
    placed = cast(OrderPlaced, event)
    return OrderPlacedIntegrationV3(
        order_id=str(placed.order_id),
        total=placed.total,
    )


class TestOutboxEntryEventVersion:
    """The integration event's declared schema version is persisted on the entry."""

    def test_entry_carries_the_default_version(self) -> None:
        entry = _registry().to_entries([_order_placed()])[0]
        assert entry.event_version == 1

    def test_entry_carries_a_subclass_version(self) -> None:
        registry = OutboundEventRegistry()
        registry.register_translation(
            OrderPlaced,
            OrderPlacedIntegrationV3,
            "orders.placed",
            _to_integration_v3,
        )

        entries = registry.to_entries([_order_placed()])

        assert entries[0].event_version == 3

    def test_version_survives_the_payload_round_trip(self) -> None:
        registry = OutboundEventRegistry()
        registry.register_translation(
            OrderPlaced,
            OrderPlacedIntegrationV3,
            "orders.placed",
            _to_integration_v3,
        )
        entry = registry.to_entries([_order_placed()])[0]

        rehydrated = registry.resolve(entry.topic).model_validate(entry.payload)

        assert rehydrated.event_version == 3  # type: ignore[attr-defined]


# ── Unit of Work integration ───────────────────────────────────────────


class Order(AggregateRoot[UUID]):
    """Sample aggregate that records one OrderPlaced event."""

    total: int = 0

    def place(self) -> None:
        """Record an OrderPlaced event."""
        self._add_event(OrderPlaced(order_id=self.id, total=self.total))


class _TestUnitOfWork(AbstractUnitOfWork):
    """Minimal UoW exposing a repository for the outbox tests."""

    def __init__(
        self,
        repo: FakeRepository[Order, UUID],
        *,
        outbox_writer: OutboxWriter | None = None,
    ) -> None:
        super().__init__(outbox_writer=outbox_writer)
        self.orders = repo
        self._repos = {"orders": repo}


class _OverridingUnitOfWork(_TestUnitOfWork):
    """UoW that replaces the outbox hook entirely."""

    async def _write_outbox(self) -> None:
        """Deliberately do nothing."""


class TestUnitOfWorkOutboxIntegration:
    """The base class wires the outbox when a writer is configured."""

    @pytest.mark.anyio
    async def test_commit_writes_translated_entries(self) -> None:
        repo: FakeRepository[Order, UUID] = FakeRepository()
        store = FakeOutboxStore()
        order = Order(id=uuid4(), total=42)
        order.place()
        await repo.save(order)
        uow = _TestUnitOfWork(repo, outbox_writer=OutboxWriter(store, _registry()))

        async with uow:
            await uow.commit()

        assert len(store.entries) == 1
        assert store.entries[0].topic == "orders.placed"

    @pytest.mark.anyio
    async def test_commit_without_a_writer_still_collects_domain_events(self) -> None:
        repo: FakeRepository[Order, UUID] = FakeRepository()
        order = Order(id=uuid4(), total=42)
        order.place()
        await repo.save(order)
        uow = _TestUnitOfWork(repo)

        async with uow:
            await uow.commit()

        # The outbox write is skipped, but the domain event path is intact.
        assert len(uow.collect_events()) == 1

    @pytest.mark.anyio
    async def test_writer_receives_stamped_events(self) -> None:
        repo: FakeRepository[Order, UUID] = FakeRepository()
        store = FakeOutboxStore()
        order = Order(id=uuid4(), total=42)
        order.place()
        await repo.save(order)
        uow = _TestUnitOfWork(repo, outbox_writer=OutboxWriter(store, _registry()))
        correlation_id = uuid4()
        # The Command Bus sets these before commit().
        setattr(uow, "_correlation_id", correlation_id)

        async with uow:
            await uow.commit()

        assert store.entries[0].correlation_id == str(correlation_id)

    @pytest.mark.anyio
    async def test_domain_events_are_still_collected(self) -> None:
        repo: FakeRepository[Order, UUID] = FakeRepository()
        store = FakeOutboxStore()
        order = Order(id=uuid4(), total=42)
        order.place()
        await repo.save(order)
        uow = _TestUnitOfWork(repo, outbox_writer=OutboxWriter(store, _registry()))

        async with uow:
            await uow.commit()

        assert len(uow.collect_events()) == 1

    @pytest.mark.anyio
    async def test_subclass_override_takes_precedence(self) -> None:
        repo: FakeRepository[Order, UUID] = FakeRepository()
        store = FakeOutboxStore()
        order = Order(id=uuid4(), total=42)
        order.place()
        await repo.save(order)
        uow = _OverridingUnitOfWork(
            repo, outbox_writer=OutboxWriter(store, _registry())
        )

        async with uow:
            await uow.commit()

        assert store.entries == []
