"""Tests for RecordingUnitOfWork — a fake that records its storage hooks."""

from __future__ import annotations

from uuid import UUID

import pytest

from pydomain.ddd import AggregateRoot, DomainEvent
from pydomain.testing import FakeRepository
from tests.support.recording_unit_of_work import RecordingUnitOfWork

pytestmark = pytest.mark.anyio


async def test_records_nothing_before_a_scope() -> None:
    assert RecordingUnitOfWork().hooks == []


async def test_records_a_commit_and_no_rollback_on_exit() -> None:
    uow = RecordingUnitOfWork()
    async with uow:
        await uow.commit()

    assert uow.hooks == ["commit"]


async def test_records_a_rollback_when_the_scope_exits_without_commit() -> None:
    uow = RecordingUnitOfWork()
    async with uow:
        pass

    assert uow.hooks == ["rollback"]


async def test_records_a_rollback_when_the_body_raises() -> None:
    uow = RecordingUnitOfWork()
    with pytest.raises(RuntimeError):
        async with uow:
            raise RuntimeError("synthetic")

    assert uow.hooks == ["rollback"]


async def test_records_an_explicit_rollback_and_the_one_on_exit() -> None:
    uow = RecordingUnitOfWork()
    async with uow:
        await uow.rollback()

    assert uow.hooks == ["rollback", "rollback"]


class _Shipped(DomainEvent):
    pass


class _Shipment(AggregateRoot[UUID]):
    pass


@pytest.mark.parametrize("by_name", [False, True])
async def test_collects_events_from_the_repositories_it_is_given(
    by_name: bool,
) -> None:
    repository: FakeRepository[_Shipment, UUID] = FakeRepository()
    shipment = _Shipment()
    shipment._add_event(_Shipped())
    await repository.save(shipment)
    if by_name:
        uow = RecordingUnitOfWork(repositories={"shipments": repository})
    else:
        uow = RecordingUnitOfWork(repository)
    async with uow:
        await uow.commit()

    assert [type(event) for event in uow.collect_events()] == [_Shipped]
