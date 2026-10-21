"""A printed SnapshotRestoreError traceback carries no stored value (value built at runtime, synthetic)."""
import asyncio, traceback, sys, io, logging
from pydomain.es import EventSourcedAggregateRoot, EventSourcedRepository, Snapshot
from pydomain.testing import FakeEventStore, FakeSnapshotStore

SENTINEL = "".join(["SEN", "TINEL", "-", "7f3a"])

class Acc(EventSourcedAggregateRoot[str]):
    total: int = 0
    def _when(self, e): pass

async def main():
    ss = FakeSnapshotStore()
    await ss.save("Acc", Snapshot(aggregate_id="a-1", version=1, state={"total": SENTINEL}))
    repo = EventSourcedRepository(event_store=FakeEventStore(), aggregate_cls=Acc, snapshot_store=ss)
    try:
        await repo.get_by_id("a-1")
    except Exception as exc:
        tb = "".join(traceback.format_exception(exc))
        buf = io.StringIO(); h = logging.StreamHandler(buf); lg = logging.getLogger("p"); lg.addHandler(h)
        lg.exception("load failed")
        print(type(exc).__name__, "| in traceback:", SENTINEL in tb, "| in log:", SENTINEL in buf.getvalue(),
              "| str/repr/args:", SENTINEL in str(exc), SENTINEL in repr(exc), SENTINEL in repr(exc.args),
              "| cause/context:", exc.__cause__, exc.__context__, "| suppress:", exc.__suppress_context__)
asyncio.run(main())
