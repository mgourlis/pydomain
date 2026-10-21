"""B-19 + CQRS-K3: locks on ONE aggregate key do not serialize the commits.

A's handler returns, the lock is released before A's commit (CQRS-K3); B takes
the lock, saves, and A's committed-exit drain takes B's event. Synthetic data.
"""
import asyncio
from uuid import UUID, uuid4
from pydomain.cqrs import Command, CommandBus, EmptyCommandResult
from pydomain.cqrs.behaviors import AggregateLockingBehavior
from pydomain.cqrs.locking import DictLockKeyResolver
from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing import FakeRepository, FakeUnitOfWork
from pydomain.testing.fake_lock_provider import FakeLockProvider

class Issued(DomainEvent):
    tag: str
class Inv(AggregateRoot[UUID]):
    @classmethod
    def issue(cls, tag):
        i = cls(id=uuid4()); i._add_event(Issued(tag=tag)); return i
class Do(Command[EmptyCommandResult]):
    tag: str

class Gated(FakeUnitOfWork):
    gate: asyncio.Event
    async def _commit(self):
        await self.gate.wait()

async def main():
    repo = FakeRepository(); provider = FakeLockProvider(); resolver = DictLockKeyResolver()
    resolver.register(Do, lambda m: ["invoice-1"])          # one key for both
    lock = [AggregateLockingBehavior(provider, resolver)]
    b_saved, a_done = asyncio.Event(), asyncio.Event()
    async def h(cmd, uow):
        await repo.save(Inv.issue(cmd.tag))
        if cmd.tag == "B":
            b_saved.set()
            await a_done.wait()   # B holds its lock until A has finished
        return EmptyCommandResult()
    def fa():
        u = Gated(repository=repo); u.gate = b_saved; return u   # A commits after B saved
    def fb():
        return FakeUnitOfWork(repository=repo)
    bus_a, bus_b = CommandBus(), CommandBus()
    bus_a.register(Do, h, uow_factory=fa, behaviors=lock)
    bus_b.register(Do, h, uow_factory=fb, behaviors=lock)
    async def da():
        try:
            _, e = await bus_a.dispatch(Do(tag="A")); return [x.tag for x in e]
        finally:
            a_done.set()
    async def db():
        await asyncio.sleep(0)
        _, e = await bus_b.dispatch(Do(tag="B")); return [x.tag for x in e]
    ra, rb = await asyncio.wait_for(asyncio.gather(da(), db()), 5)
    print("one key, gated commits: A ->", ra, " B ->", rb)

asyncio.run(main())
