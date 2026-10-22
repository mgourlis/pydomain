"""Run the ``UnitOfWork`` contract suite against ``FakeUnitOfWork``."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from pydomain.cqrs.unit_of_work import UnitOfWork
from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing import FakeRepository, FakeUnitOfWork
from pydomain.testing.contracts import UnitOfWorkContract


class InvoiceIssued(DomainEvent):
    invoice_id: UUID


class Invoice(AggregateRoot[UUID]):
    @classmethod
    def issue(cls) -> Invoice:
        invoice = cls(id=uuid4())
        invoice._add_event(InvoiceIssued(invoice_id=invoice.id))
        return invoice


class InvoiceUnitOfWork(FakeUnitOfWork):
    def __init__(self) -> None:
        self.invoices = FakeRepository[Invoice, UUID]()
        super().__init__(repository=self.invoices)


class TestFakeUnitOfWorkContract(UnitOfWorkContract):
    @pytest.fixture
    def unit_of_work(self) -> InvoiceUnitOfWork:
        return InvoiceUnitOfWork()

    async def stage_change(self, uow: UnitOfWork) -> int:
        assert isinstance(uow, InvoiceUnitOfWork)
        await uow.invoices.save(Invoice.issue())
        return 1
