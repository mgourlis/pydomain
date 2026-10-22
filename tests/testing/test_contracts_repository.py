"""Run the ``Repository`` contract suite against ``FakeRepository``."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing import FakeRepository
from pydomain.testing.contracts import RepositoryContract


class OrderPlaced(DomainEvent):
    order_id: UUID


class OrderConfirmed(DomainEvent):
    order_id: UUID


class Order(AggregateRoot[UUID]):
    status: str = "new"

    @classmethod
    def place(cls) -> Order:
        order = cls(id=uuid4(), status="placed")
        order._add_event(OrderPlaced(order_id=order.id))
        return order

    def confirm(self) -> None:
        self.status = "confirmed"
        self._add_event(OrderConfirmed(order_id=self.id))


class TestFakeRepositoryContract(RepositoryContract):
    @pytest.fixture
    def repository(self) -> FakeRepository[Order, UUID]:
        return FakeRepository[Order, UUID]()

    def new_aggregate(self) -> Order:
        return Order.place()

    def change(self, aggregate: Order) -> None:
        aggregate.confirm()

    @pytest.mark.xfail(
        strict=True,
        raises=pytest.fail.Exception,
        reason=(
            "FAKE-01, fixed by W2-6: get_by_id returns the stored "
            "object, so both copies are one object and no conflict is seen"
        ),
    )
    @pytest.mark.anyio
    async def test_saving_a_stale_copy_raises_concurrency_error(
        self, repository: Any
    ) -> None:
        await super().test_saving_a_stale_copy_raises_concurrency_error(repository)
