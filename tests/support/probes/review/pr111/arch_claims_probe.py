"""Probe runtime claims the DCE-119 docs make about snapshot restore (synthetic data)."""
import asyncio, traceback
from typing import Any, ClassVar
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, computed_field, model_validator

from pydomain.ddd import DomainEvent
from pydomain.es import EventSourcedAggregateRoot, EventSourcedRepository, Snapshot
from pydomain.testing import FakeEventStore, FakeSnapshotStore

try:
    from pydomain.es import SnapshotRestoreError
except ImportError:
    SnapshotRestoreError = None

class Added(DomainEvent):
    agg_id: str
    n: int

class Acc(EventSourcedAggregateRoot[str]):
    total: int = 0
    label: str = "x"
    _cache: int = PrivateAttr(default=-1)
    def model_post_init(self, ctx: Any) -> None:
        super().model_post_init(ctx)
        self._cache = self.total * 10
    def add(self, n: int) -> None:
        self._apply(Added(agg_id=self.id, n=n))
    def _when(self, e: DomainEvent) -> None:
        assert isinstance(e, Added)
        self.total += e.n

class Forbid(EventSourcedAggregateRoot[str]):
    model_config = ConfigDict(extra="forbid")
    total: int = 0
    @computed_field
    @property
    def double(self) -> int:
        return self.total * 2
    def _when(self, e: DomainEvent) -> None:
        pass

calls = []
class WithValidator(EventSourcedAggregateRoot[str]):
    total: int = 0
    @model_validator(mode="after")
    def _count(self):
        calls.append(self.total)
        return self
    def _when(self, e): pass

async def case(name, coro):
    try:
        r = await coro
        print(f"[{name}] ->", r)
    except Exception as exc:
        print(f"[{name}] RAISED {type(exc).__name__}: {exc}")
        print("   cause:", repr(exc.__cause__), "context:", repr(exc.__context__))
        if SnapshotRestoreError and isinstance(exc, SnapshotRestoreError):
            print("   attrs:", exc.aggregate_id, exc.snapshot_version, "DomainError:", type(exc).__mro__[1].__name__)

async def load(cls, state, version=1, ev=0):
    es, ss = FakeEventStore(), FakeSnapshotStore()
    agg = cls(id="a-1")
    for i in range(ev):
        agg.add(1)
    if ev:
        await es.append_to_stream("a-1", agg.pull_events(), 0)
    await ss.save(cls.__name__, Snapshot(aggregate_id="a-1", version=version, state=state))
    repo = EventSourcedRepository(event_store=es, aggregate_cls=cls, snapshot_store=ss)
    return await repo.get_by_id("a-1")

async def main():
    # Removed field key, default extra
    await case("removed key, default extra", load(Acc, {"id": "a-1", "total": 3, "gone": 1}))
    # Removed field key, forbid
    await case("removed key, forbid", load(Forbid, {"id": "a-1", "total": 3, "gone": "SECRET-VALUE"}))
    # Computed key, forbid (old snapshot)
    await case("computed key, forbid", load(Forbid, {"id": "a-1", "total": 3, "double": 6}))
    # Changed type that coerces ("5" -> int) -- docs say 'a changed type raises'
    await case("str '5' into int (lax coercion)", load(Acc, {"id": "a-1", "total": "5"}))
    await case("float 2.0 into int", load(Acc, {"id": "a-1", "total": 2.0}))
    await case("int into str field", load(Acc, {"id": "a-1", "total": 1, "label": 7}))
    await case("bad value", load(Acc, {"id": "a-1", "total": "SECRET-VALUE"}))
    # Private attr derived in model_post_init: snapshot path vs full replay
    es, ss = FakeEventStore(), FakeSnapshotStore()
    a = Acc(id="a-1"); a.add(2); a.add(3)
    await es.append_to_stream("a-1", a.pull_events(), 0)
    snap = Acc(id="a-1"); snap.total = 2; snap.version = 1
    await ss.save("Acc", snap._take_snapshot())
    r1 = await EventSourcedRepository(event_store=es, aggregate_cls=Acc, snapshot_store=ss).get_by_id("a-1")
    r2 = await EventSourcedRepository(event_store=es, aggregate_cls=Acc).get_by_id("a-1")
    print("[private via model_post_init] snapshot path total/_cache:", r1.total, r1._cache, "| full replay:", r2.total, r2._cache)
    # Validators run on load
    calls.clear()
    await case("validator runs", load(WithValidator, {"id": "a-1", "total": 4}))
    print("   validator calls:", calls)
    # Traceback printing must not contain value
    try:
        await load(Acc, {"id": "a-1", "total": "SECRET-VALUE"})
    except Exception as exc:
        tb = "".join(traceback.format_exception(exc))
        print("[traceback contains SECRET-VALUE]", "SECRET-VALUE" in tb, "| repr:", "SECRET-VALUE" in repr(exc))

asyncio.run(main())
