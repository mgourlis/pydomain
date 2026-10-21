import asyncio
from pydomain.testing import FakeEventStore
from pydomain.ddd.domain_event import DomainEvent
class Happened(DomainEvent):
    n: int
async def main():
    es = FakeEventStore()
    await es.append_to_stream("a", [Happened(n=i) for i in range(5)], expected_version=0)
    for v in (0, 3, -1, -2):
        s = await es.read_stream("a", from_version=v)
        print(f"read_stream(from_version={v:>2}) -> {[e.n for e in s.events]} version={s.version}")
    s = await es.read_all(from_version=-1)
    print(f"read_all(from_version=-1) -> {[e.n for e in s.events]}")
asyncio.run(main())
