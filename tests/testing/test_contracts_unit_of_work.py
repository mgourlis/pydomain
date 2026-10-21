"""Run the ``UnitOfWork`` contract suite against ``FakeUnitOfWork``."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from pydomain.cqrs.unit_of_work import UnitOfWork
from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing import FakeRepository, FakeUnitOfWork
from pydomain.testing.contracts import UnitOfWorkContract

_K5 = "DCE-116 CQRS-K5: rollback leaves the repository's buffered events"
_CQRS_24 = "DCE-116 CQRS-24: a clean exit without commit does not roll back"
_CQRS_08 = "DCE-116 CQRS-08: collect_events returns the internal list"
_CQRS_10 = "DCE-116 D-CQRS-10: a second commit in one scope does not raise"


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

    @pytest.mark.xfail(strict=True, raises=AssertionError, reason=_K5)
    @pytest.mark.anyio
    async def test_rollback_discards_the_staged_events(
        self, unit_of_work: UnitOfWork
    ) -> None:
        await super().test_rollback_discards_the_staged_events(unit_of_work)

    @pytest.mark.xfail(strict=True, raises=AssertionError, reason=_K5)
    @pytest.mark.anyio
    async def test_rollback_twice_is_harmless(self, unit_of_work: UnitOfWork) -> None:
        await super().test_rollback_twice_is_harmless(unit_of_work)

    @pytest.mark.xfail(strict=True, raises=AssertionError, reason=_CQRS_24)
    @pytest.mark.anyio
    async def test_exit_without_commit_discards_the_staged_events(
        self, unit_of_work: UnitOfWork
    ) -> None:
        await super().test_exit_without_commit_discards_the_staged_events(unit_of_work)

    @pytest.mark.xfail(strict=True, raises=AssertionError, reason=_K5)
    @pytest.mark.anyio
    async def test_error_inside_context_discards_the_staged_events(
        self, unit_of_work: UnitOfWork
    ) -> None:
        await super().test_error_inside_context_discards_the_staged_events(unit_of_work)

    @pytest.mark.xfail(strict=True, raises=AssertionError, reason=_CQRS_08)
    @pytest.mark.anyio
    async def test_collect_events_returns_a_copy(
        self, unit_of_work: UnitOfWork
    ) -> None:
        await super().test_collect_events_returns_a_copy(unit_of_work)

    @pytest.mark.xfail(strict=True, raises=pytest.fail.Exception, reason=_CQRS_10)
    @pytest.mark.anyio
    async def test_second_commit_in_one_scope_raises(
        self, unit_of_work: UnitOfWork
    ) -> None:
        await super().test_second_commit_in_one_scope_raises(unit_of_work)
