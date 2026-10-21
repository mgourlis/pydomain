"""An old snapshot written under serialize_by_alias with a serialization-only alias (synthetic)."""
import asyncio
from pydantic import ConfigDict, Field
from pydomain.es import EventSourcedAggregateRoot, EventSourcedRepository, Snapshot
from pydomain.testing import FakeEventStore, FakeSnapshotStore

class Ledger(EventSourcedAggregateRoot[str]):
    model_config = ConfigDict(serialize_by_alias=True)
    balance: int = Field(default=0, serialization_alias="Balance")
    def _when(self, e): pass

class Plain(EventSourcedAggregateRoot[str]):
    model_config = ConfigDict(serialize_by_alias=True)
    balance: int = Field(default=0, alias="Balance")
    def _when(self, e): pass

async def main():
    for cls in (Ledger, Plain):
        a = cls(id="a-1") if cls is Ledger else cls(id="a-1", Balance=0)
        a.balance = 42
        old_state = a.model_dump()           # how the dev branch dumped it
        old_state.pop("version", None)
        ss = FakeSnapshotStore()
        await ss.save(cls.__name__, Snapshot(aggregate_id="a-1", version=1, state=old_state))
        try:
            r = await EventSourcedRepository(event_store=FakeEventStore(), aggregate_cls=cls, snapshot_store=ss).get_by_id("a-1")
            print(cls.__name__, "old state", old_state, "-> balance", r.balance)
        except Exception as exc:
            print(cls.__name__, "old state", old_state, "-> RAISED", type(exc).__name__, exc)
asyncio.run(main())
