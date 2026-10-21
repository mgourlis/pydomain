"""Re-review probes for PR #111 at bbdc004 (synthetic data)."""
import asyncio, json, traceback
from datetime import datetime, UTC
from enum import Enum
from typing import Any
from uuid import UUID, uuid4
from pydantic import (AliasChoices, AliasPath, BaseModel, ConfigDict, Field, PrivateAttr,
                      computed_field, model_validator)
from pydantic.alias_generators import to_camel
from pydomain.ddd import DomainEvent
from pydomain.es import EventSourcedAggregateRoot, EventSourcedRepository, Snapshot, SnapshotRestoreError
from pydomain.testing import FakeEventStore, FakeSnapshotStore

SENT = "".join(["SEN", "TINEL-", "91c"])

async def load(cls, state, version=1, name=None):
    ss = FakeSnapshotStore()
    await ss.save(name or cls.__name__, Snapshot(aggregate_id="a-1", version=version, state=state))
    return await EventSourcedRepository(event_store=FakeEventStore(), aggregate_cls=cls, snapshot_store=ss).get_by_id("a-1")

async def roundtrip(agg):
    cls = type(agg)
    snap = agg._take_snapshot()
    return snap, await load(cls, snap.state, version=snap.version)

async def case(name, coro):
    try:
        r = await coro
        print(f"[{name}] ->", r)
    except BaseException as exc:
        tb = "".join(traceback.format_exception(exc))
        print(f"[{name}] RAISED {type(exc).__name__}: {exc} | sentinel in tb: {SENT in tb} | cause/ctx: {exc.__cause__!r} {exc.__context__!r}")

class Inner(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: int = 1
    @computed_field
    @property
    def twice(self) -> int:
        return self.a * 2

class NestedForbid(EventSourcedAggregateRoot[str]):
    inner: Inner = Field(default_factory=Inner)
    def _when(self, e): pass

class Camel(EventSourcedAggregateRoot[str]):
    model_config = ConfigDict(alias_generator=to_camel, serialize_by_alias=True, validate_by_name=False)
    order_total: int = 0
    def _when(self, e): pass

class SerOnly(EventSourcedAggregateRoot[str]):
    total: int = Field(default=0, serialization_alias="Total")
    def _when(self, e): pass

class ValOnly(EventSourcedAggregateRoot[str]):
    total: int = Field(default=0, validation_alias="TOTAL")
    def _when(self, e): pass

class PathAlias(EventSourcedAggregateRoot[str]):
    total: int = Field(default=0, validation_alias=AliasPath("wrap", 0))
    def _when(self, e): pass

seen = []
class VersionSeen(EventSourcedAggregateRoot[str]):
    total: int = 0
    @model_validator(mode="after")
    def _v(self):
        seen.append(self.version)
        return self
    def _when(self, e): pass

class Color(Enum):
    RED = "red"

class Strict(EventSourcedAggregateRoot[str]):
    model_config = ConfigDict(strict=True)
    at: datetime = Field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    ref: UUID = Field(default_factory=uuid4)
    color: Color = Color.RED
    def _when(self, e): pass

class Raises(EventSourcedAggregateRoot[str]):
    total: int = 0
    @model_validator(mode="after")
    def _v(self):
        if self.total > 5:
            raise KeyError(f"bad {self.total} {SENT}")
        return self
    def _when(self, e): pass

class PostInitRaises(EventSourcedAggregateRoot[str]):
    total: int = 0
    def model_post_init(self, ctx: Any) -> None:
        if self.total:
            raise RuntimeError(SENT)
    def _when(self, e): pass

class KbInt(EventSourcedAggregateRoot[str]):
    total: int = 0
    @model_validator(mode="after")
    def _v(self):
        if self.total == 7:
            raise KeyboardInterrupt
        return self
    def _when(self, e): pass

class DictField(EventSourcedAggregateRoot[str]):
    tags: dict[str, int] = Field(default_factory=dict)
    def _when(self, e): pass

async def main():
    # nested computed key in an OLD snapshot under nested extra=forbid
    await case("old snapshot, nested computed key, nested forbid", load(NestedForbid, {"inner": {"a": 3, "twice": 6}}))
    a = NestedForbid(id="a-1"); a.inner = Inner(a=3)
    snap, r = await roundtrip(a); print("[new snapshot nested computed] state:", snap.state, "->", r)
    # aliases
    c = Camel(id="a-1"); c.order_total = 4
    snap, r = await roundtrip(c); print("[alias_generator + serialize_by_alias] state:", snap.state, "->", r.order_total)
    await case("Camel alias-keyed old snapshot", load(Camel, {"orderTotal": 9}))
    s = SerOnly(id="a-1"); s.total = 4
    snap, r = await roundtrip(s); print("[serialization_alias only]", snap.state, "->", r.total)
    await case("SerOnly old alias-keyed snapshot ('Total')", load(SerOnly, {"Total": 9}))
    v = ValOnly(id="a-1"); v.total = 4
    snap, r = await roundtrip(v); print("[validation_alias only]", snap.state, "->", r.total)
    p = PathAlias(id="a-1"); p.total = 4
    snap, r = await roundtrip(p); print("[AliasPath]", snap.state, "->", r.total)
    # version validators see
    seen.clear(); await load(VersionSeen, {"total": 1}, version=5); print("[version seen by validator]", seen)
    # strict mode: python-mode store, and JSON round-trip
    st = Strict(id="a-1"); snap, r = await roundtrip(st); print("[strict, python store] ok:", r is not None)
    js = json.loads(json.dumps(snap.state, default=str))
    for k in ("at", "ref", "color"):
        state = dict(snap.state); state[k] = js[k]
        await case(f"strict, JSON-typed {k}", load(Strict, state))
    # exception wrapping
    await case("validator raises KeyError w/ value", load(Raises, {"total": 9}))
    await case("model_post_init raises RuntimeError", load(PostInitRaises, {"total": 1}))
    await case("validator raises KeyboardInterrupt", load(KbInt, {"total": 7}))
    # dict keys appear in location
    await case("dict key in location", load(DictField, {"tags": {SENT: "x"}}))

asyncio.run(main())
