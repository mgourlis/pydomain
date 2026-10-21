"""Run the ``Repository`` contract suite against ``FakeRepository``."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing import FakeRepository
from pydomain.testing.contracts import RepositoryContract


class OrderPlaced(DomainEvent):
    order_id: UUID


class Order(AggregateRoot[UUID]):
    status: str = "new"

    @classmethod
    def place(cls) -> Order:
        order = cls(id=uuid4(), status="placed")
        order._add_event(OrderPlaced(order_id=order.id))
        return order


class TestFakeRepositoryContract(RepositoryContract):
    @pytest.fixture
    def repository(self) -> FakeRepository[Order, UUID]:
        return FakeRepository[Order, UUID]()

    def new_aggregate(self) -> Order:
        return Order.place()
