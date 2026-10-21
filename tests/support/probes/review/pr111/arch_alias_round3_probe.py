"""Round-3 probes for B-74/B-78 alias handling on restore (synthetic data)."""
import asyncio, traceback
from pydantic import AliasChoices, AliasPath, BaseModel, ConfigDict, Field
from pydomain.es import EventSourcedAggregateRoot, EventSourcedRepository, Snapshot
from pydomain.testing import FakeEventStore, FakeSnapshotStore

SENT = "".join(["SEN", "TINEL-", "b3"])

async def load(cls, state):
    ss = FakeSnapshotStore()
    await ss.save(cls.__name__, Snapshot(aggregate_id="a-1", version=1, state=state))
    return await EventSourcedRepository(event_store=FakeEventStore(), aggregate_cls=cls, snapshot_store=ss).get_by_id("a-1")

async def case(name, cls, state, show):
    try:
        r = await load(cls, state)
        print(f"[{name}] ->", show(r))
    except Exception as exc:
        tb = "".join(traceback.format_exception(exc))
        print(f"[{name}] RAISED {type(exc).__name__}: {exc} | sentinel in tb: {SENT in tb}")

class SerOnly(EventSourcedAggregateRoot[str]):
    model_config = ConfigDict(serialize_by_alias=True)
    balance: int = Field(default=0, serialization_alias="Balance")
    def _when(self, e): pass

class Collide(EventSourcedAggregateRoot[str]):   # alias of a equals name of b
    a: int = Field(default=0, alias="b")
    b: int = 0
    def _when(self, e): pass

class Choices(EventSourcedAggregateRoot[str]):
    total: int = Field(default=0, validation_alias=AliasChoices("Total", "TOTAL"))
    def _when(self, e): pass

class Inner(BaseModel):
    amount: int = Field(default=0, alias="Amount")

class Nested(EventSourcedAggregateRoot[str]):
    inner: Inner = Field(default_factory=Inner)
    def _when(self, e): pass

class SharedAlias(EventSourcedAggregateRoot[str]):  # two fields claim one serialization alias
    x: int = Field(default=0, serialization_alias="Val")
    y: int = Field(default=0, serialization_alias="Val")
    def _when(self, e): pass

class IdAlias(EventSourcedAggregateRoot[str]):
    total: int = Field(default=0, alias="version")   # alias equals a field name of the base
    def _when(self, e): pass

async def main():
    await case("M1 serialization-only alias key", SerOnly, {"Balance": 42}, lambda r: r.balance)
    await case("collision a(alias b) / b by name", Collide, {"a": 1, "b": 2}, lambda r: (r.a, r.b))
    await case("ambiguous name+alias", SerOnly, {"balance": 1, "Balance": SENT}, lambda r: r.balance)
    await case("ambiguous two AliasChoices keys", Choices, {"Total": 1, "TOTAL": 2}, lambda r: r.total)
    await case("AliasChoices one key", Choices, {"TOTAL": 7}, lambda r: r.total)
    await case("nested alias key (old snapshot)", Nested, {"inner": {"Amount": 5}}, lambda r: r.inner.amount)
    await case("two fields share one serialization alias", SharedAlias, {"Val": 3}, lambda r: (r.x, r.y))
    await case("alias equals base field 'version'", IdAlias, {"total": 4}, lambda r: (r.total, r.version))
    n = Nested(id="a-1"); n.inner = Inner(Amount=5)
    snap = n._take_snapshot(); r = await load(Nested, snap.state); print("[nested new snapshot]", snap.state, "->", r.inner.amount)

asyncio.run(main())
