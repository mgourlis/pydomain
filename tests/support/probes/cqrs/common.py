"""Shared synthetic fixtures for the CQRS probes (order/invoice style)."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

from pydomain.cqrs.command_bus import CommandBus
from pydomain.cqrs.commands import Command, CommandResult
from pydomain.cqrs.unit_of_work import AbstractUnitOfWork
from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent


class OrderPlaced(DomainEvent):
    order_id: UUID


class ItemAdded(DomainEvent):
    order_id: UUID
    sku: str


class InvoiceIssued(DomainEvent):
    invoice_id: UUID


class Order(AggregateRoot[UUID]):
    def place(self) -> None:
        self._add_event(OrderPlaced(order_id=self.id))

    def add_item(self, sku: str) -> None:
        self._add_event(ItemAdded(order_id=self.id, sku=sku))


class PlaceOrderResult(CommandResult):
    order_id: UUID


class PlaceOrder(Command[PlaceOrderResult]):
    order_id: UUID


class BufferRepo:
    """Minimal repository: save() buffers aggregate events, pull_events drains."""

    def __init__(self) -> None:
        self.buffer: list[DomainEvent] = []
        self.staged: list[Any] = []

    async def save(self, agg: Any) -> None:
        self.staged.append(agg)
        self.buffer.extend(agg.pull_events())

    def pull_events(self) -> list[DomainEvent]:
        ev, self.buffer = self.buffer, []
        return ev


class SpyUoW(AbstractUnitOfWork):
    """UoW with a spy log, a durable 'database', and fault injection."""

    def __init__(self, log: list[str], db: dict[str, Any], repo: BufferRepo | None = None,
                 fail_commit: int = 0, commit_delay: float = 0.0, outbox_writer: Any = None,
                 rollback_raises_after: int | None = None) -> None:
        super().__init__(outbox_writer=outbox_writer)
        self.log = log
        self.db = db
        self.orders = repo or BufferRepo()
        self._repos = {"orders": self.orders}
        self.fail_commit = fail_commit
        self.commit_delay = commit_delay
        self.rollbacks = 0
        self.rollback_raises_after = rollback_raises_after

    async def _commit(self) -> None:
        if self.commit_delay:
            await asyncio.sleep(self.commit_delay)
        if self.fail_commit:
            self.fail_commit -= 1
            self.log.append("commit-FAILED")
            raise RuntimeError("synthetic commit failure")
        for agg in self.orders.staged:
            self.db[str(agg.id)] = agg
        self.orders.staged.clear()
        self.log.append("commit")

    async def rollback(self) -> None:
        self.rollbacks += 1
        self.log.append(f"rollback#{self.rollbacks}")
        if self.rollback_raises_after is not None and self.rollbacks > self.rollback_raises_after:
            raise RuntimeError(f"synthetic rollback failure #{self.rollbacks}")
        self.orders.staged.clear()
        await super().rollback()


def new_id() -> UUID:
    return uuid4()


__all__ = [
    "BufferRepo", "CommandBus", "InvoiceIssued", "ItemAdded", "Order", "OrderPlaced",
    "PlaceOrder", "PlaceOrderResult", "SpyUoW", "new_id",
]
