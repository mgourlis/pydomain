"""Round-3 probes for PR #111 at c30b6ca: alias renaming and ambiguity. Prints observations."""
from __future__ import annotations

import asyncio
from typing import Any

from pydantic import AliasChoices, AliasPath, ConfigDict, Field, computed_field
from pydantic.alias_generators import to_camel

from pydomain.ddd import DomainEvent
from pydomain.es.aggregate import EventSourcedAggregateRoot
from pydomain.es.event_sourced_repository import EventSourcedRepository, _top_level_aliases
from pydomain.es.snapshot import Snapshot, SnapshotThresholdPolicy
from pydomain.testing import FakeEventStore, FakeSnapshotStore


class Ev(DomainEvent):
    n: int


class Base(EventSourcedAggregateRoot[str]):
    def _when(self, e: DomainEvent) -> None:
        pass


async def load_state(cls: type[Any], state: dict[str, Any]) -> Any:
    es, ss = FakeEventStore(), FakeSnapshotStore()
    await es.append_to_stream("a-1", [Ev(n=1)], expected_version=0)
    await ss.save(cls.__name__, Snapshot(aggregate_id="a-1", version=1, state=state))
    try:
        loaded = await EventSourcedRepository(es, cls, snapshot_store=ss).get_by_id("a-1")
        return {k: v for k, v in loaded.model_dump(by_alias=False).items() if k not in ("id", "version")}
    except BaseException as exc:  # noqa: BLE001
        return f"RAISED {type(exc).__name__}: {exc}"


async def roundtrip(agg: Any) -> Any:
    cls = type(agg)
    es, ss = FakeEventStore(), FakeSnapshotStore()
    repo = EventSourcedRepository(es, cls, snapshot_store=ss, snapshot_policy=SnapshotThresholdPolicy(0))
    agg._apply(Ev(n=1))
    await repo.save(agg)
    try:
        loaded = await repo.get_by_id(agg.id)
    except Exception as exc:  # noqa: BLE001
        return f"RAISED {type(exc).__name__}: {exc}"
    return f"equal={loaded.model_dump() == agg.model_dump()} {loaded.model_dump(by_alias=False)}"


class Collide(Base):
    a: int = Field(default=0, alias="b")
    b: int = 0


class Swap(Base):
    model_config = ConfigDict(populate_by_name=True)
    a: int = Field(default=0, alias="b")
    b: int = Field(default=0, alias="a")


class NameOnlyCollide(Base):
    model_config = ConfigDict(validate_by_alias=False, validate_by_name=True)
    a: int = Field(default=0, alias="b")
    b: int = 0


class Generated(Base):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    line_total: int = 0
    unit_count: int = 0


class GeneratedSBA(Generated):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, serialize_by_alias=True)


class Kinds(Base):
    model_config = ConfigDict(populate_by_name=True)
    p1: int = Field(default=0, validation_alias=AliasPath("one"))
    p2: int = Field(default=0, validation_alias=AliasPath("outer", "inner"))
    p3: int = Field(default=0, validation_alias=AliasPath("lst", 0))
    va: int = Field(default=0, validation_alias="vOnly")
    sa: int = Field(default=0, serialization_alias="sOnly")
    both: int = Field(default=0, validation_alias="bV", serialization_alias="bS")
    ch: int = Field(default=0, validation_alias=AliasChoices("c1", AliasPath("c2"), AliasPath("c3", "x")))


class Renamed(Base):
    """The rename idiom: a new field that still reads its old key."""
    total: int = Field(default=0, validation_alias=AliasChoices("total", "qty"))


class ComputedAlias(Base):
    model_config = ConfigDict(extra="forbid")
    w: list[int] = Field(default_factory=list)

    @computed_field(alias="Weight")  # type: ignore[prop-decorator]
    @property
    def weight(self) -> int:
        return sum(self.w)


async def main() -> None:
    for cls in (Collide, Swap, NameOnlyCollide, Generated, Kinds, Renamed):
        print(f"{cls.__name__} renames: {_top_level_aliases(cls)}")
    print("Collide round trip:", await roundtrip(Collide(id="c", b=0)) if False else await roundtrip(Collide.model_validate({"id": "c", "b": 1, "a": 0}, by_name=True)))
    print("Collide {'a':1,'b':2}:", await load_state(Collide, {"a": 1, "b": 2}))
    print("Swap {'a':1,'b':2}:", await load_state(Swap, {"a": 1, "b": 2}))
    print("NameOnlyCollide {'a':1,'b':2}:", await load_state(NameOnlyCollide, {"a": 1, "b": 2}))
    print("Generated alias-keyed old state:", await load_state(Generated, {"lineTotal": 3, "unitCount": 4}))
    print("GeneratedSBA round trip:", await roundtrip(GeneratedSBA(id="g", line_total=5)))
    print("Kinds one-element AliasPath key 'one':", await load_state(Kinds, {"one": 1}))
    print("Kinds multi AliasPath nested {'outer':{'inner':2}}:", await load_state(Kinds, {"outer": {"inner": 2}}))
    print("Kinds vOnly / sOnly / bV / bS:", await load_state(Kinds, {"vOnly": 3, "sOnly": 4, "bS": 5}))
    print("Kinds AliasChoices c1, c2:", await load_state(Kinds, {"c1": 6}), await load_state(Kinds, {"c2": 7}))
    print("Kinds both validation and serialization alias for one field:", await load_state(Kinds, {"bV": 1, "bS": 2}))
    print("Renamed: old key only {'qty':3}:", await load_state(Renamed, {"qty": 3}))
    print("Renamed: name + removed-field key {'total':5,'qty':3}:", await load_state(Renamed, {"total": 5, "qty": 3}))
    print("Renamed: plain pydantic resolves it as:", Renamed.model_validate({"id": "x", "total": 5, "qty": 3}).total)
    print("ComputedAlias old alias-keyed computed key + forbid:", await load_state(ComputedAlias, {"w": [1], "Weight": 1}))
    print("Ambiguity message value-free:", await load_state(Collide, {"a": 1, "b": 2}) )
    print("Name + alias for one field (Generated):", await load_state(Generated, {"line_total": 1, "lineTotal": 2}))


asyncio.run(main())
