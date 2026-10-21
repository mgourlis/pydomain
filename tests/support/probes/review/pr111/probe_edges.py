"""Probes for PR #111: snapshot restore edge cases. Prints observations."""
from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, computed_field, model_validator, AliasChoices

from pydomain.ddd import DomainEvent
from pydomain.ddd.exceptions import DomainError
from pydomain.es.aggregate import EventSourcedAggregateRoot
from pydomain.es.event_sourced_repository import EventSourcedRepository
from pydomain.es.snapshot import Snapshot, SnapshotThresholdPolicy
from pydomain.testing import FakeEventStore, FakeSnapshotStore

SENT = "SENTINEL-c0ffee"


class Billed(DomainEvent):
    agg: str
    qty: int


async def roundtrip(cls: type[Any], build=None, threshold=0, events=2):
    """Apply events, save with snapshot, load; return (original, loaded or exc)."""
    es, ss = FakeEventStore(), FakeSnapshotStore()
    repo = EventSourcedRepository(es, cls, snapshot_store=ss, snapshot_policy=SnapshotThresholdPolicy(threshold))
    agg = cls(id="a-1") if build is None else build()
    for n in range(events):
        agg._apply(Billed(agg="a-1", qty=n + 1))
    await repo.save(agg)
    snap = await ss.get(cls.__name__, "a-1")
    try:
        loaded = await repo.get_by_id("a-1")
    except BaseException as exc:  # noqa: BLE001
        return agg, snap, exc
    return agg, snap, loaded


def show(name, res):
    agg, snap, out = res
    if isinstance(out, BaseException):
        print(f"[{name}] RAISED {type(out).__name__}: {out}")
        print(f"    cause={type(out.__cause__).__name__ if out.__cause__ else None} context={type(out.__context__).__name__ if out.__context__ else None}")
        tb = "".join(traceback.format_exception(out))
        print(f"    sentinel in traceback: {SENT in tb}")
    else:
        print(f"[{name}] loaded == original dump: {out.model_dump() == agg.model_dump()}  loaded={out.model_dump()}  original={agg.model_dump()}")
    if snap is not None:
        print(f"    snapshot state keys: {sorted(snap.state)}")


# 1. nested model with computed field and extra=forbid
class Box(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weights: list[int] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> int:
        return sum(self.weights)


class NestedComputed(EventSourcedAggregateRoot[str]):
    box: Box = Field(default_factory=Box)

    def _when(self, e: DomainEvent) -> None:
        self.box.weights.append(e.qty)


# 2. aliased field without populate_by_name
class Aliased(EventSourcedAggregateRoot[str]):
    total: int = Field(default=0, alias="totalQty")

    def _when(self, e: DomainEvent) -> None:
        self.total += e.qty


class AliasedRequired(EventSourcedAggregateRoot[str]):
    label: str = Field(alias="Label")

    def _when(self, e: DomainEvent) -> None:
        self.label = self.label + "x"


class ValidationAliased(EventSourcedAggregateRoot[str]):
    total: int = Field(default=0, validation_alias=AliasChoices("qty_total", "q"))

    def _when(self, e: DomainEvent) -> None:
        self.total += e.qty


class AliasedByName(EventSourcedAggregateRoot[str]):
    model_config = ConfigDict(populate_by_name=True)
    total: int = Field(default=0, alias="totalQty")

    def _when(self, e: DomainEvent) -> None:
        self.total += e.qty


# 3. validator raising DomainError (pydomain's own invariant idiom), with value in message
class Guarded(EventSourcedAggregateRoot[str]):
    note: str = ""
    qty: int = 0

    @model_validator(mode="after")
    def _invariant(self) -> Self:
        if self.qty > 100:
            raise DomainError(f"qty too large for note {self.note!r}")
        return self

    def _when(self, e: DomainEvent) -> None:
        self.qty += e.qty


# 4. model_post_init raising
class PostInitBoom(EventSourcedAggregateRoot[str]):
    note: str = ""

    def model_post_init(self, ctx: Any, /) -> None:
        if self.note.startswith("SENTINEL"):
            raise KeyError(self.note)

    def _when(self, e: DomainEvent) -> None:
        pass


# 5. dict field: key leaks via loc
class Tags(EventSourcedAggregateRoot[str]):
    tags: dict[str, int] = Field(default_factory=dict)

    def _when(self, e: DomainEvent) -> None:
        pass


# 6. validate_assignment, frozen sub-model, private attrs, pending events
class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)
    a: int = 0


class Rich(EventSourcedAggregateRoot[str]):
    model_config = ConfigDict(validate_assignment=True)
    fz: Frozen = Field(default_factory=Frozen)
    fzs: list[Frozen] = Field(default_factory=list)
    _cache: list[int] = PrivateAttr(default_factory=list)

    def _when(self, e: DomainEvent) -> None:
        self.fz = Frozen(a=e.qty)
        self.fzs = [*self.fzs, Frozen(a=e.qty)]
        self._cache.append(e.qty)


# 7. validator raising ValueError with value in message
class VE(EventSourcedAggregateRoot[str]):
    note: str = ""

    @model_validator(mode="before")
    @classmethod
    def _chk(cls, data: Any) -> Any:
        if isinstance(data, dict) and str(data.get("note", "")).startswith("SENTINEL"):
            raise ValueError(f"bad note {data['note']}")
        return data

    def _when(self, e: DomainEvent) -> None:
        pass


async def load_state(cls, state, version=2):
    es, ss = FakeEventStore(), FakeSnapshotStore()
    await es.append_to_stream("a-1", [Billed(agg="a-1", qty=1)] * version, expected_version=0)
    await ss.save(cls.__name__, Snapshot(aggregate_id="a-1", version=version, state=state))
    try:
        return await EventSourcedRepository(es, cls, snapshot_store=ss).get_by_id("a-1")
    except BaseException as exc:  # noqa: BLE001
        return exc


def show_exc(name, out):
    if isinstance(out, BaseException):
        tb = "".join(traceback.format_exception(out))
        print(f"[{name}] RAISED {type(out).__name__}: {out!s}  | sentinel in str/traceback: {SENT in str(out)}/{SENT in tb}")
        print(f"    cause={out.__cause__!r} context={type(out.__context__).__name__ if out.__context__ else None}")
    else:
        print(f"[{name}] loaded {out.model_dump()}")


async def main():
    show("nested computed + nested extra=forbid", await roundtrip(NestedComputed))
    show("alias, default", await roundtrip(Aliased, build=lambda: Aliased(id="a-1")))
    show("alias, required", await roundtrip(AliasedRequired, build=lambda: AliasedRequired(id="a-1", Label="L")))
    show("validation_alias", await roundtrip(ValidationAliased))
    show("alias + populate_by_name", await roundtrip(AliasedByName))
    r = await roundtrip(Rich)
    show("validate_assignment + frozen submodels + private", r)
    out = r[2]
    if not isinstance(out, BaseException):
        print(f"    types: fz={type(out.fz).__name__} fzs={[type(x).__name__ for x in out.fzs]} version={out.version} pending={out.pull_events()}")
        print(f"    original version={r[0].version}")
    show_exc("validator raises DomainError (value in msg)", await load_state(Guarded, {"note": SENT, "qty": 500}))
    show_exc("model_post_init raises KeyError", await load_state(PostInitBoom, {"note": SENT}))
    show_exc("dict key in loc", await load_state(Tags, {"tags": {SENT: "not-int"}}))
    show_exc("before-validator ValueError with value", await load_state(VE, {"note": SENT}))
    show_exc("state carries a different id", await load_state(Tags, {"id": "other-id", "tags": {}}))
    show_exc("state carries version 99", await load_state(Tags, {"version": 99, "tags": {}}))


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    asyncio.run(main())
