"""Probe: content sentinel on SnapshotRestoreError across value kinds, reprs, tracebacks and logs.

Run from the worktree root with PYTHONPATH=src:. so tests.support is importable.
"""
from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from pydomain.ddd import DomainEvent
from pydomain.es.aggregate import EventSourcedAggregateRoot
from pydomain.es.event_sourced_repository import EventSourcedRepository
from pydomain.es.snapshot import Snapshot
from pydomain.testing import FakeEventStore, FakeSnapshotStore
from tests.support.content_sentinel import CONTENT_SENTINEL as S, assert_absent_from_logs, assert_absent_from_reprs


class Line(BaseModel):
    sku: str
    quantity: int


class Ev(DomainEvent):
    n: int


class Order(EventSourcedAggregateRoot[str]):
    lines: list[Line] = Field(default_factory=list)
    count: int = 0
    tags: dict[str, int] = Field(default_factory=dict)

    def _when(self, e: DomainEvent) -> None:
        pass


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


CASES = {
    "nested str in int": {"lines": [{"sku": "x", "quantity": S}]},
    "top-level str in int": {"count": S},
    "missing required nested + sentinel sibling": {"lines": [{"sku": S}]},
    "wrong container": {"lines": S},
    "dict value": {"tags": {"k": S}},
    "dict key (documented ADR-071 limit)": {"tags": {S: "x"}},
}


async def main() -> None:
    cap = Capture()
    root = logging.getLogger()
    root.addHandler(cap)
    root.setLevel(logging.DEBUG)
    for name, state in CASES.items():
        es, ss = FakeEventStore(), FakeSnapshotStore()
        await es.append_to_stream("o-1", [Ev(n=1)], expected_version=0)
        await ss.save("Order", Snapshot(aggregate_id="o-1", version=1, state=state))
        try:
            await EventSourcedRepository(es, Order, snapshot_store=ss).get_by_id("o-1")
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("app").exception("load failed: %s", exc)
            try:
                assert_absent_from_reprs(exc)
                assert_absent_from_logs(cap.records)
                print(f"[{name}] clean: {type(exc).__name__}: {exc}")
            except AssertionError as leak:
                print(f"[{name}] LEAK: {leak}")
            cap.records.clear()


asyncio.run(main())
