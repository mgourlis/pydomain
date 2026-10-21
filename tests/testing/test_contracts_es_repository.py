"""Run the ``Repository`` contract suite against ``EventSourcedRepository``."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from pydomain.ddd.domain_event import DomainEvent
from pydomain.es.aggregate import EventSourcedAggregateRoot
from pydomain.es.event_sourced_repository import EventSourcedRepository
from pydomain.testing import FakeEventStore
from pydomain.testing.contracts import RepositoryContract

_NO_DELETE = (
    "EventSourcedRepository has no delete; it does not satisfy the Repository "
    "Protocol — reported on DCE-105"
)


class ShipmentBooked(DomainEvent):
    shipment_id: UUID


class ShipmentDelayed(DomainEvent):
    days: int


class Shipment(EventSourcedAggregateRoot[UUID]):
    status: str = "new"
    delay_days: int = 0

    @classmethod
    def book(cls) -> Shipment:
        shipment = cls(id=uuid4())
        shipment._apply(ShipmentBooked(shipment_id=shipment.id))
        return shipment

    def delay(self, days: int) -> None:
        self._apply(ShipmentDelayed(days=days))

    def _when(self, event: DomainEvent) -> None:
        if isinstance(event, ShipmentBooked):
            self.status = "booked"
        elif isinstance(event, ShipmentDelayed):
            self.delay_days += event.days
        else:
            raise ValueError(f"Unknown event: {event!r}")


class TestEventSourcedRepositoryContract(RepositoryContract):
    @pytest.fixture
    def repository(self) -> EventSourcedRepository[Shipment, UUID]:
        return EventSourcedRepository(FakeEventStore(), Shipment)

    def new_aggregate(self) -> Shipment:
        return Shipment.book()

    def change(self, aggregate: Shipment) -> None:
        aggregate.delay(days=1)

    @pytest.mark.xfail(strict=True, raises=AssertionError, reason=_NO_DELETE)
    @pytest.mark.anyio
    async def test_satisfies_protocol(self, repository: Any) -> None:
        await super().test_satisfies_protocol(repository)

    @pytest.mark.xfail(strict=True, raises=AttributeError, reason=_NO_DELETE)
    @pytest.mark.anyio
    async def test_delete_removes_aggregate(self, repository: Any) -> None:
        await super().test_delete_removes_aggregate(repository)

    @pytest.mark.xfail(strict=True, raises=AttributeError, reason=_NO_DELETE)
    @pytest.mark.anyio
    async def test_delete_of_unknown_id_does_not_raise(self, repository: Any) -> None:
        await super().test_delete_of_unknown_id_does_not_raise(repository)

    @pytest.mark.xfail(
        strict=True,
        raises=pytest.fail.Exception,
        reason=(
            "DCE-110: the case saves an aggregate with no pending events, which a "
            "correct event-sourced save ignores; replace it with a lost update"
        ),
    )
    @pytest.mark.anyio
    async def test_save_with_mismatched_version_raises_concurrency_error(
        self, repository: Any
    ) -> None:
        await super().test_save_with_mismatched_version_raises_concurrency_error(
            repository
        )
