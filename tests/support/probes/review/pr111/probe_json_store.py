"""Probe: restore through a store that serializes snapshots to JSON (as a real adapter does)."""
from __future__ import annotations

import asyncio
import enum
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from pydomain.ddd import DomainEvent
from pydomain.es.aggregate import EventSourcedAggregateRoot
from pydomain.es.event_sourced_repository import EventSourcedRepository
from pydomain.es.snapshot import Snapshot, SnapshotThresholdPolicy
from pydomain.testing import FakeEventStore


class JsonSnapshotStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], str] = {}

    async def save(self, aggregate_type: str, snapshot: Snapshot) -> None:
        self.rows[(aggregate_type, snapshot.aggregate_id)] = snapshot.model_dump_json()

    async def get(self, aggregate_type: str, aggregate_id: str) -> Snapshot | None:
        raw = self.rows.get((aggregate_type, aggregate_id))
        return None if raw is None else Snapshot.model_validate_json(raw)


class Colour(enum.Enum):
    RED = "red"


class Part(BaseModel):
    model_config = ConfigDict(frozen=True)
    code: str
    price: Decimal


class Added(DomainEvent):
    n: int


class Rich(EventSourcedAggregateRoot[UUID]):
    when: datetime | None = None
    colour: Colour | None = None
    parts: tuple[Part, ...] = ()
    tags: set[str] = Field(default_factory=set)
    by_code: dict[str, Part] = Field(default_factory=dict)
    maybe: Part | None = None

    def _when(self, e: DomainEvent) -> None:
        p = Part(code=f"c{e.n}", price=Decimal("1.10") * e.n)
        self.when = datetime(2026, 1, e.n, tzinfo=timezone.utc)
        self.colour = Colour.RED
        self.parts = (*self.parts, p)
        self.tags = self.tags | {p.code}
        self.by_code = {**self.by_code, p.code: p}
        self.maybe = p


class StrictRich(Rich):
    model_config = ConfigDict(strict=True)


async def run(cls: type[Rich]) -> None:
    es, ss = FakeEventStore(), JsonSnapshotStore()
    repo = EventSourcedRepository(es, cls, snapshot_store=ss, snapshot_policy=SnapshotThresholdPolicy(2))
    agg = cls(id=uuid4())
    for n in (1, 2):
        agg._apply(Added(n=n))
    await repo.save(agg)
    agg._apply(Added(n=3))
    await repo.save(agg)
    try:
        loaded = await repo.get_by_id(agg.id)
    except Exception as exc:  # noqa: BLE001
        print(f"[{cls.__name__}] RAISED {type(exc).__name__}: {exc}")
        return
    full = await EventSourcedRepository(es, cls).get_by_id(agg.id)
    print(f"[{cls.__name__}] snapshot+tail == full replay: {loaded.model_dump() == full.model_dump()}; types ok: "
          f"{isinstance(loaded.id, UUID) and isinstance(loaded.parts[0], Part) and isinstance(loaded.by_code['c1'], Part)}")


asyncio.run(run(Rich))
asyncio.run(run(StrictRich))
