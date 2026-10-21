"""Round-2 probes for PR #111 at bbdc004. Prints observations."""
from __future__ import annotations

import asyncio
import traceback
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pydomain.ddd import DomainEvent
from pydomain.es.aggregate import EventSourcedAggregateRoot
from pydomain.es.event_sourced_repository import EventSourcedRepository
from pydomain.es.snapshot import Snapshot, SnapshotThresholdPolicy
from pydomain.testing import FakeEventStore, FakeSnapshotStore


class Ev(DomainEvent):
    n: int


async def roundtrip(agg: Any) -> Any:
    cls = type(agg)
    es, ss = FakeEventStore(), FakeSnapshotStore()
    repo = EventSourcedRepository(es, cls, snapshot_store=ss, snapshot_policy=SnapshotThresholdPolicy(0))
    agg._apply(Ev(n=1))
    await repo.save(agg)
    snap = await ss.get(cls.__name__, agg.id)
    try:
        loaded = await repo.get_by_id(agg.id)
    except Exception as exc:  # noqa: BLE001
        return snap, exc
    return snap, loaded


async def load_state(cls: type[Any], state: dict[str, Any]) -> Any:
    es, ss = FakeEventStore(), FakeSnapshotStore()
    await es.append_to_stream("a-1", [Ev(n=1)], expected_version=0)
    await ss.save(cls.__name__, Snapshot(aggregate_id="a-1", version=1, state=state))
    try:
        return await EventSourcedRepository(es, cls, snapshot_store=ss).get_by_id("a-1")
    except BaseException as exc:  # noqa: BLE001
        return exc


def show(name: str, out: Any, original: Any = None) -> None:
    if isinstance(out, BaseException):
        print(f"[{name}] RAISED {type(out).__name__}: {out}")
    else:
        same = "" if original is None else f" equal-to-original={out.model_dump() == original}"
        print(f"[{name}] loaded {out.model_dump()}{same}")


# 1. Alias collision: field a has alias "b", and a separate field named b.
class Swap(EventSourcedAggregateRoot[str]):
    model_config = ConfigDict(populate_by_name=True)
    a: int = Field(default=0, alias="b")
    b: int = Field(default=0, alias="a")

    def _when(self, e: DomainEvent) -> None:
        self.a, self.b = 1, 2


class Collide(EventSourcedAggregateRoot[str]):
    a: int = Field(default=0, alias="b")
    b: int = 0

    def _when(self, e: DomainEvent) -> None:
        self.a, self.b = 1, 2


# 2. Config combinations: snapshot keyed by name, round trip.
class ByAliasOnlyRequired(EventSourcedAggregateRoot[str]):
    model_config = ConfigDict(validate_by_alias=True, validate_by_name=False)
    total: int = Field(default=0, alias="T")

    def _when(self, e: DomainEvent) -> None:
        self.total += 5


class NameOnly(EventSourcedAggregateRoot[str]):
    model_config = ConfigDict(validate_by_alias=False, validate_by_name=True)
    total: int = Field(default=0, alias="T")

    def _when(self, e: DomainEvent) -> None:
        self.total += 5


# 3. Nested model with serialize_by_alias=True: does by_alias=False reach it?
class InnerSBA(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True, extra="forbid")
    qty: int = Field(default=0, alias="Qty")


class OuterSBA(EventSourcedAggregateRoot[str]):
    inner: InnerSBA = Field(default_factory=InnerSBA)

    def _when(self, e: DomainEvent) -> None:
        self.inner = InnerSBA(Qty=7)


# 4. Programming error in the model: forward ref never resolved.
class Broken(EventSourcedAggregateRoot[str]):
    later: "Undefined | None" = None  # noqa: F821

    def _when(self, e: DomainEvent) -> None:
        pass


# 5. RecursionError / MemoryError in model_post_init are Exceptions.
class Deep(EventSourcedAggregateRoot[str]):
    def model_post_init(self, ctx: Any, /) -> None:
        if self.version:
            raise MemoryError

    def _when(self, e: DomainEvent) -> None:
        pass


async def main() -> None:
    for agg in (Swap(id="s"), Collide(id="c"), ByAliasOnlyRequired(id="r"), NameOnly(id="n"), OuterSBA(id="o")):
        original = None
        agg2 = type(agg).model_validate(agg.model_dump(by_alias=True))  # noqa: F841
        snap, out = await roundtrip(agg)
        print(f"    {type(agg).__name__} snapshot state: {snap.state}")
        show(type(agg).__name__, out, agg.model_dump())
    # alias-keyed and mixed old states for the collision models
    show("Collide alias-keyed old state {'b': 1} (meant a=1)", await load_state(Collide, {"b": 1}))
    show("Collide name-keyed state {'a':1,'b':2}", await load_state(Collide, {"a": 1, "b": 2}))
    show("Swap name-keyed state {'a':1,'b':2}", await load_state(Swap, {"a": 1, "b": 2}))
    try:
        Broken(id="x")
    except Exception as exc:  # noqa: BLE001
        print(f"[Broken direct construct] {type(exc).__name__}")
    show("Broken via snapshot", await load_state(Broken, {}))
    try:
        await EventSourcedRepository(FakeEventStore(), Broken).get_by_id("zzz")
    except Exception as exc:  # noqa: BLE001
        print(f"[Broken full replay] {type(exc).__name__}")
    show("MemoryError in post_init", await load_state(Deep, {}))


asyncio.run(main())


# 6. validate_by_alias=False model whose alias collides with another field's name.
class NameOnlyCollide(EventSourcedAggregateRoot[str]):
    model_config = ConfigDict(validate_by_alias=False, validate_by_name=True)
    a: int = Field(default=0, alias="b")
    b: int = 0

    def _when(self, e: DomainEvent) -> None:
        self.a, self.b = 1, 2


async def main2() -> None:
    agg = NameOnlyCollide(a=1, b=2, id="x")
    print("    plain model_validate of the dump:", NameOnlyCollide.model_validate(agg.model_dump()).model_dump())
    snap, out = await roundtrip(NameOnlyCollide(id="nc"))
    print(f"    NameOnlyCollide snapshot state: {snap.state}")
    show("NameOnlyCollide restore", out)


asyncio.run(main2())
